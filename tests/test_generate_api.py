from contextlib import closing
import sqlite3
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
import create_chrome_profile as ccp


class GenerateApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.profiles_dir = Path(self.temporary_directory.name)
        (self.profiles_dir / "profile-one").mkdir()
        self._media_cache_dir = server.MEDIA_CACHE_DIR
        self._history_db_path = server.HISTORY_DB_PATH
        server.MEDIA_CACHE_DIR = self.profiles_dir / "media-cache"
        server.HISTORY_DB_PATH = server.MEDIA_CACHE_DIR / "history.sqlite3"
        server.ARGS = SimpleNamespace(
            profiles_dir=self.profiles_dir,
            dry_run=False,
            chrome=None,
            port=8000,
        )
        self.client = server.app.test_client()
        self.headers = {"X-UI-Token": server.TOKEN}

    def tearDown(self):
        server.MEDIA_CACHE_DIR = self._media_cache_dir
        server.HISTORY_DB_PATH = self._history_db_path
        self.temporary_directory.cleanup()

    def test_generate_accepts_closed_profile_and_starts_job(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"job-one"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                json={
                    "profile_id": "profile-one",
                    "prompt": "Generate Gambar, 16:9\nMake a picture",
                    "media_type": "image",
                    "ratio": "16:9",
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], server._generate_extension_job)
        self.assertEqual(launch.call_args.args[2].profile, "profile-one")
        self.assertEqual(launch.call_args.args[3:], (
            "profile-one",
            "Generate Gambar, 16:9\nMake a picture",
        ))
        history = self.client.get("/api/history", headers=self.headers).get_json()
        self.assertEqual(history[0]["media_type"], "image")
        self.assertEqual(history[0]["ratio"], "16:9")
        self.assertEqual(history[0]["prompt"], "Generate Gambar, 16:9\nMake a picture")

    def test_generate_job_opens_closed_profile_then_sends_prompt(self):
        job = server.GenerateJob("profile-one", "video", "9:16")
        with (
            patch("server.running_profiles", return_value=({}, None)),
            patch("server.GENERATE_BRIDGE.status", side_effect=[
                {"extension_ready": False},
                {"extension_ready": True},
            ]),
            patch("server.GENERATE_BRIDGE.issue_pairing", return_value="pair-ticket"),
            patch("server.ccp.open_profile", return_value=0) as open_profile,
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "detail": "Prompt dikirim.",
            }) as send_prompt,
            patch("server._save_generated_media", return_value={
                "filename": "job1234567.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 100,
            }),
        ):
            result = server._generate_extension_job(job, "profile-one", "Make a video")

        self.assertEqual(result, 0)
        open_profile.assert_called_once_with(
            None,
            self.profiles_dir,
            "profile-one",
            "http://127.0.0.1:8000/pair#pair-ticket",
            False,
        )
        send_prompt.assert_called_once()
        self.assertEqual(send_prompt.call_args.args[:2], ("profile-one", "Make a video"))

    def test_generate_job_uses_open_profile_without_reopening_it(self):
        job = server.GenerateJob("profile-one", "image", "16:9")
        with (
            patch("server.GENERATE_BRIDGE.status", side_effect=[
                {"extension_ready": False},
                {"extension_ready": True},
            ]),
            patch("server.running_profiles", return_value=({"profile-one": [123]}, None)),
            patch("server.ccp.open_profile") as open_profile,
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "detail": "Prompt dikirim.",
            }) as send_prompt,
            patch("server._save_generated_media", return_value={
                "filename": "job1234567.png",
                "mime_type": "image/png",
                "size_bytes": 100,
            }),
        ):
            result = server._generate_extension_job(job, "profile-one", "Make a video")

        self.assertEqual(result, 0)
        open_profile.assert_not_called()
        send_prompt.assert_called_once()

    def test_generate_job_receives_job_profile_and_prompt(self):
        with (
            patch("server.running_profiles", return_value=({"profile-one": [123]}, None)),
            patch("server.GENERATE_BRIDGE.status", return_value={
                "extension_paired": True,
                "extension_ready": True,
                "pairing": False,
            }),
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "detail": "Prompt dikirim.",
            }) as send_prompt,
            patch("server._save_generated_media", return_value={
                "filename": "job1234567.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 100,
            }),
        ):
            response = self.client.post(
                "/api/generate",
                json={"profile_id": "profile-one", "prompt": "Make a video"},
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200)
            job_id = response.get_json()["id"]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status_response = self.client.get(
                    f"/api/jobs/{job_id}",
                    headers=self.headers,
                )
                if status_response.get_json()["status"] != "running":
                    break
                time.sleep(0.01)

        self.assertEqual(status_response.get_json()["status"], "done")
        send_prompt.assert_called_once()
        self.assertEqual(send_prompt.call_args.args[:2], ("profile-one", "Make a video"))

    def test_generate_rejects_empty_prompt(self):
        response = self.client.post(
            "/api/generate",
            json={"profile_id": "profile-one", "prompt": "  "},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Prompt kosong.")

    def test_generate_flow_routes_video_to_flow_provider(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                json={
                    "profile_id": "profile-one",
                    "prompt": "A calm ocean at sunrise",
                    "provider": "flow",
                    "media_type": "video",
                    "ratio": "16:9",
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], server._generate_flow_job)
        self.assertEqual(launch.call_args.args[2].provider, "flow")
        self.assertEqual(launch.call_args.args[2].flow_model, "Omni 1.1 Flash")
        self.assertEqual(launch.call_args.args[2].flow_resolution, "720p")
        self.assertEqual(launch.call_args.args[2].flow_duration, "10 dtk")
        self.assertEqual(launch.call_args.args[2].ratio, "16:9")
        history = self.client.get("/api/history", headers=self.headers).get_json()
        self.assertEqual(history[0]["provider"], "flow")

    def test_generate_flow_rejects_unsupported_model(self):
        response = self.client.post(
            "/api/generate",
            json={
                "profile_id": "profile-one",
                "prompt": "A calm ocean at sunrise",
                "provider": "flow",
                "flow_model": "unlisted-model",
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Model Flow tidak didukung.")

    def test_generate_flow_accepts_image_mode_and_defaults_model(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-image-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                json={
                    "profile_id": "profile-one",
                    "prompt": "A calm ocean at sunrise",
                    "provider": "flow",
                    "media_type": "image",
                    "ratio": "1:1",
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], server._generate_flow_job)
        self.assertEqual(launch.call_args.args[2].media_type, "image")
        self.assertEqual(launch.call_args.args[2].flow_model, "Nano Banana 2.1")
        self.assertEqual(launch.call_args.args[2].ratio, "1:1")

    def test_generate_flow_rejects_video_model_for_image(self):
        response = self.client.post(
            "/api/generate",
            json={
                "profile_id": "profile-one",
                "prompt": "A calm ocean at sunrise",
                "provider": "flow",
                "media_type": "image",
                "flow_model": "Omni 1.1 Flash",
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Model Flow tidak didukung.")

    def test_generate_flow_accepts_image_aspect_ratios(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-image-job"}',
            status=200,
            mimetype="application/json",
        )):
            response = self.client.post(
                "/api/generate",
                json={
                    "profile_id": "profile-one",
                    "prompt": "A calm ocean at sunrise",
                    "provider": "flow",
                    "media_type": "image",
                    "ratio": "4:3",
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)

    def test_generate_flow_accepts_reference_image_upload(self):
        image_data = b"\x89PNG\r\n\x1a\nreference-image"
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-image-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Transform the reference image",
                    "provider": "flow",
                    "media_type": "image",
                    "ratio": "1:1",
                    "reference_image": (BytesIO(image_data), "reference.png"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        job = launch.call_args.args[2]
        self.assertIs(launch.call_args.args[1], server._generate_flow_job)
        self.assertEqual(job.reference_image_path.suffix, ".png")
        self.assertEqual(job.reference_image_path.read_bytes(), image_data)
        job.reference_image_path.unlink()

    def test_generate_rejects_reference_image_for_other_modes(self):
        response = self.client.post(
            "/api/generate",
            data={
                "profile_id": "profile-one",
                "prompt": "A calm ocean",
                "provider": "gemini",
                "media_type": "image",
                "reference_image": (BytesIO(b"\x89PNG\r\n\x1a\nimage"), "reference.png"),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Gambar referensi hanya didukung oleh Google Flow.",
        )

    def test_generate_rejects_invalid_reference_image_data(self):
        response = self.client.post(
            "/api/generate",
            data={
                "profile_id": "profile-one",
                "prompt": "A calm ocean",
                "provider": "flow",
                "media_type": "image",
                "reference_image": (BytesIO(b"not an image"), "reference.png"),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Format gambar referensi harus PNG, JPG, atau WEBP.",
        )

    def test_generate_rejects_oversized_reference_image(self):
        image_data = b"\x89PNG\r\n\x1a\nlarge"
        with patch("server.MAX_REFERENCE_IMAGE_BYTES", len(image_data) - 1):
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "A calm ocean",
                    "provider": "flow",
                    "media_type": "image",
                    "reference_image": (BytesIO(image_data), "reference.png"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Ukuran gambar referensi maksimal 20 MB.",
        )

    def test_generate_flow_accepts_reference_video_upload(self):
        video_data = b"\x00\x00\x00\x18ftypisomreference-video"
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-video-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Transform the reference video",
                    "provider": "flow",
                    "media_type": "video",
                    "ratio": "16:9",
                    "flow_model": "Omni 1.1 Flash",
                    "reference_video": (BytesIO(video_data), "reference.mp4"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        job = launch.call_args.args[2]
        self.assertIs(launch.call_args.args[1], server._generate_flow_job)
        self.assertEqual(job.reference_video_path.suffix, ".mp4")
        self.assertEqual(job.reference_video_path.read_bytes(), video_data)
        job.reference_video_path.unlink()

    def test_generate_rejects_reference_video_for_other_modes(self):
        response = self.client.post(
            "/api/generate",
            data={
                "profile_id": "profile-one",
                "prompt": "Transform the reference video",
                "provider": "gemini",
                "media_type": "video",
                "reference_video": (
                    BytesIO(b"\x00\x00\x00\x18ftypisomreference-video"),
                    "reference.mp4",
                ),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Video referensi hanya didukung oleh Google Flow.",
        )

    def test_generate_accepts_reference_video_with_any_video_model(self):
        video_data = b"\x00\x00\x00\x18ftypisomreference-video"
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-video-veo-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Transform the reference video",
                    "provider": "flow",
                    "media_type": "video",
                    "flow_model": "Veo 3.1 - Fast",
                    "reference_media": (BytesIO(video_data), "reference.mp4"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        job = launch.call_args.args[2]
        self.assertEqual(job.reference_video_path.read_bytes(), video_data)
        job.reference_video_path.unlink()

    def test_generate_accepts_image_reference_for_video_output(self):
        image_data = b"\x89PNG\r\n\x1a\nreference-image"
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-video-image-reference-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Use this image to make a video",
                    "provider": "flow",
                    "media_type": "video",
                    "reference_media": (BytesIO(image_data), "reference.png"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        job = launch.call_args.args[2]
        self.assertEqual(job.reference_image_path.read_bytes(), image_data)
        job.reference_image_path.unlink()

    def test_generate_accepts_video_reference_for_image_output(self):
        video_data = b"\x00\x00\x00\x18ftypisomreference-video"
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"flow-image-video-reference-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Use this video to make an image",
                    "provider": "flow",
                    "media_type": "image",
                    "flow_model": "Nano Banana 2.1",
                    "reference_media": (BytesIO(video_data), "reference.mp4"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        job = launch.call_args.args[2]
        self.assertEqual(job.reference_video_path.read_bytes(), video_data)
        job.reference_video_path.unlink()

    def test_generate_rejects_invalid_reference_video_data(self):
        response = self.client.post(
            "/api/generate",
            data={
                "profile_id": "profile-one",
                "prompt": "Transform the reference video",
                "provider": "flow",
                "media_type": "video",
                "reference_video": (BytesIO(b"not a video"), "reference.mp4"),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Isi video referensi tidak cocok dengan format file.",
        )

    def test_generate_rejects_oversized_reference_video(self):
        video_data = b"\x00\x00\x00\x18ftypisomlarge"
        with patch("server.MAX_REFERENCE_VIDEO_BYTES", len(video_data) - 1):
            response = self.client.post(
                "/api/generate",
                data={
                    "profile_id": "profile-one",
                    "prompt": "Transform the reference video",
                    "provider": "flow",
                    "media_type": "video",
                    "reference_video": (BytesIO(video_data), "reference.mp4"),
                },
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "Ukuran video referensi maksimal 100 MB.",
        )

    def test_generate_rejects_both_reference_media(self):
        response = self.client.post(
            "/api/generate",
            data={
                "profile_id": "profile-one",
                "prompt": "Transform the reference media",
                "provider": "flow",
                "media_type": "video",
                "reference_image": (BytesIO(b"\x89PNG\r\n\x1a\nimage"), "reference.png"),
                "reference_video": (
                    BytesIO(b"\x00\x00\x00\x18ftypisomreference-video"),
                    "reference.mp4",
                ),
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Pilih satu media referensi saja.")

    def test_history_database_migrates_provider_for_existing_rows(self):
        server.MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(server.HISTORY_DB_PATH)) as connection:
            connection.execute("""
                CREATE TABLE generations (
                    job_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, profile_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL, ratio TEXT NOT NULL,
                    prompt TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '', mime_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute(
                """INSERT INTO generations
                   (job_id, created_at, profile_id, media_type, ratio, prompt, status)
                   VALUES ('0123456789', '2026-11-04', 'profile-one', 'video', '9:16', 'prompt', 'complete')"""
            )
            connection.commit()

        with closing(server._history_connection()) as connection:
            row = connection.execute(
                "SELECT provider FROM generations WHERE job_id='0123456789'"
            ).fetchone()
        self.assertEqual(row["provider"], "gemini")

    def test_generate_flow_downloads_and_records_video(self):
        job = server.GenerateJob("profile-one", "video", "16:9", "flow")
        server._create_generation_record(job, "A calm ocean at sunrise")
        downloads_dir = self.profiles_dir / "Downloads"
        downloads_dir.mkdir()
        downloaded = downloads_dir / "flow-video.mp4"
        downloaded.write_bytes(b"\x00\x00\x00\x18ftypisomflow")
        with (
            patch("server._ensure_generate_extension_connected"),
            patch("server.Path.home", return_value=self.profiles_dir),
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "download_path": str(downloaded),
                "mime_type": "video/mp4",
            }) as send_prompt,
        ):
            result = server._generate_flow_job(job, "profile-one", "A calm ocean at sunrise")

        self.assertEqual(result, 0)
        send_prompt.assert_called_once()
        self.assertEqual(send_prompt.call_args.kwargs["provider"], "flow")
        self.assertEqual(send_prompt.call_args.kwargs["flow_model"], "Omni 1.1 Flash")
        self.assertEqual(send_prompt.call_args.kwargs["flow_resolution"], "720p")
        media = server.MEDIA_CACHE_DIR / f"{job.id}.mp4"
        self.assertEqual(media.read_bytes(), downloaded.read_bytes())
        history = self.client.get("/api/history", headers=self.headers).get_json()
        self.assertEqual(history[0]["provider"], "flow")
        self.assertEqual(history[0]["status"], "complete")
        self.assertEqual(history[0]["size_bytes"], downloaded.stat().st_size)

    def test_generate_flow_downloads_and_records_image(self):
        job = server.GenerateJob("profile-one", "image", "1:1", "flow", flow_model="Nano Banana 2.1")
        server._create_generation_record(job, "A calm ocean at sunrise")
        downloads_dir = self.profiles_dir / "Downloads"
        downloads_dir.mkdir()
        downloaded = downloads_dir / "flow-image.png"
        downloaded.write_bytes(b"\x89PNG\r\n\x1a\nflow-image")
        with (
            patch("server._ensure_generate_extension_connected"),
            patch("server.Path.home", return_value=self.profiles_dir),
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "download_path": str(downloaded),
                "mime_type": "image/png",
            }) as send_prompt,
        ):
            result = server._generate_flow_job(job, "profile-one", "A calm ocean at sunrise")

        self.assertEqual(result, 0)
        self.assertEqual(send_prompt.call_args.kwargs["provider"], "flow")
        self.assertEqual(send_prompt.call_args.kwargs["flow_model"], "Nano Banana 2.1")
        self.assertEqual(send_prompt.call_args.kwargs["ratio"], "1:1")
        media = server.MEDIA_CACHE_DIR / f"{job.id}.png"
        self.assertEqual(media.read_bytes(), downloaded.read_bytes())
        history = self.client.get("/api/history", headers=self.headers).get_json()
        self.assertEqual(history[0]["provider"], "flow")
        self.assertEqual(history[0]["media_type"], "image")
        self.assertEqual(history[0]["mime_type"], "image/png")
        self.assertEqual(history[0]["status"], "complete")
        self.assertEqual(history[0]["size_bytes"], downloaded.stat().st_size)

    def test_generate_flow_passes_reference_media_url_to_extension(self):
        job = server.GenerateJob("profile-one", "image", "1:1", "flow", flow_model="Nano Banana 2.1")
        reference_image = server.MEDIA_CACHE_DIR / f"{job.id}.flow-reference.png"
        reference_image.parent.mkdir(parents=True, exist_ok=True)
        reference_image.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        job.reference_image_path = reference_image
        server._create_generation_record(job, "Transform the reference image")
        downloads_dir = self.profiles_dir / "Downloads"
        downloads_dir.mkdir()
        downloaded = downloads_dir / "flow-image.png"
        downloaded.write_bytes(b"\x89PNG\r\n\x1a\nflow-image")
        with (
            patch("server._ensure_generate_extension_connected"),
            patch("server.Path.home", return_value=self.profiles_dir),
            patch("server.GENERATE_BRIDGE.send_prompt", return_value={
                "status": "complete",
                "download_path": str(downloaded),
                "mime_type": "image/png",
            }) as send_prompt,
        ):
            result = server._generate_flow_job(job, "profile-one", "Transform the reference image")

        self.assertEqual(result, 0)
        self.assertEqual(send_prompt.call_args.kwargs["reference_name"], reference_image.name)
        self.assertIn(f"/extension-media/{job.id}/", send_prompt.call_args.kwargs["reference_url"])
        self.assertTrue(job.reference_token == "")

    def test_extension_reference_media_requires_job_token(self):
        job = server.GenerateJob("profile-one", "image", "1:1", "flow")
        job.reference_token = "one-time-reference-token"
        reference_image = self.profiles_dir / "reference.png"
        reference_image.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        job.reference_image_path = reference_image
        server.JOBS[job.id] = job
        try:
            valid = self.client.get(
                f"/extension-media/{job.id}/{job.reference_token}",
                headers={"Host": "127.0.0.1:8000"},
            )
            invalid = self.client.get(
                f"/extension-media/{job.id}/wrong-token",
                headers={"Host": "127.0.0.1:8000"},
            )
            self.assertEqual(valid.status_code, 200)
            self.assertEqual(valid.data, reference_image.read_bytes())
            self.assertEqual(valid.headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(invalid.status_code, 404)
            valid.close()
            invalid.close()
        finally:
            server.JOBS.pop(job.id, None)

    def test_generate_job_cleans_up_uploaded_reference_media(self):
        job = server.GenerateJob("profile-one", "video", "16:9", "flow")
        reference_image = server.MEDIA_CACHE_DIR / f"{job.id}.flow-reference.png"
        reference_video = server.MEDIA_CACHE_DIR / f"{job.id}.flow-reference.mp4"
        reference_image.parent.mkdir(parents=True, exist_ok=True)
        reference_image.write_bytes(b"\x89PNG\r\n\x1a\nreference")
        reference_video.write_bytes(b"\x00\x00\x00\x18ftypisomreference")
        job.reference_image_path = reference_image
        job.reference_video_path = reference_video

        started = server.start_job(job, lambda: 0)
        deadline = time.monotonic() + 2
        while (
            (started.active or reference_image.exists() or reference_video.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        self.assertEqual(started.status, "done")
        self.assertFalse(reference_image.exists())
        self.assertFalse(reference_video.exists())

    def test_generate_jobs_for_same_profile_are_queued_sequentially(self):
        profile_id = f"queue-profile-{time.monotonic_ns()}"
        first_started = threading.Event()
        release_first = threading.Event()
        execution_order = []

        def first_job():
            execution_order.append("first-start")
            first_started.set()
            release_first.wait(2)
            execution_order.append("first-end")

        first = server.GenerateJob(profile_id, "image", "16:9")
        second = server.GenerateJob(profile_id, "image", "16:9")
        server.start_job(first, first_job)
        self.assertTrue(first_started.wait(2))
        server.start_job(second, lambda: execution_order.append("second"))

        self.assertEqual(second.status, "queued")
        self.assertTrue(second.active)
        with self.assertRaises(RuntimeError):
            server.start_job(server.Job("Other action", profile_id), lambda: 0)

        release_first.set()
        deadline = time.monotonic() + 2
        while (first.active or second.active) and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(first.status, "done")
        self.assertEqual(second.status, "done")
        self.assertEqual(execution_order, ["first-start", "first-end", "second"])

    def test_server_open_flow_starts_profile_job(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"open-flow-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/profiles/profile-one/flow/open",
                json={},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], server._open_flow_profile)
        self.assertEqual(launch.call_args.args[2], "profile-one")

    def test_open_flow_opens_profile_without_cdp(self):
        with (
            patch("server.GENERATE_BRIDGE.status", return_value={"extension_ready": True}),
            patch("server.ccp.open_profile", return_value=0) as open_profile,
        ):
            result = server._open_flow_profile("profile-one")

        self.assertEqual(result, 0)
        open_profile.assert_called_once_with(
            None,
            self.profiles_dir,
            "profile-one",
            "https://flow.google.com/",
            False,
        )

    def test_open_flow_pairs_extension_and_routes_back_to_flow(self):
        with (
            patch("server.GENERATE_BRIDGE.status", return_value={"extension_ready": False}),
            patch("server.GENERATE_BRIDGE.issue_pairing", return_value="pair-ticket"),
            patch("server.ccp.open_profile", return_value=0) as open_profile,
        ):
            result = server._open_flow_profile("profile-one")

        self.assertEqual(result, 0)
        self.assertEqual(
            open_profile.call_args.args,
            (
                None,
                self.profiles_dir,
                "profile-one",
                "http://127.0.0.1:8000/pair?next=flow#pair-ticket",
                False,
            ),
        )

    def test_media_download_is_copied_into_persistent_cache(self):
        downloads_dir = self.profiles_dir / "Downloads"
        downloads_dir.mkdir()
        source = downloads_dir / "gemini.png"
        source.write_bytes(b"image-data")
        job = server.GenerateJob("profile-one", "image", "9:16")
        with patch("server.Path.home", return_value=self.profiles_dir):
            saved = server._save_generated_media(job, {
                "download_path": str(source),
                "mime_type": "image/png",
            })

        cached = server.MEDIA_CACHE_DIR / saved["filename"]
        self.assertEqual(cached.read_bytes(), b"image-data")
        self.assertEqual(saved["size_bytes"], len(b"image-data"))

    def test_media_download_rejects_path_outside_downloads(self):
        source = self.profiles_dir / "not-downloads.png"
        source.write_bytes(b"image-data")
        job = server.GenerateJob("profile-one", "image", "9:16")
        with (
            patch("server.Path.home", return_value=self.profiles_dir),
            self.assertRaisesRegex(RuntimeError, "folder Downloads standar"),
        ):
            server._save_generated_media(job, {
                "download_path": str(source),
                "mime_type": "image/png",
            })

    def test_history_media_route_serves_only_cache_files(self):
        server.MEDIA_CACHE_DIR.mkdir(parents=True)
        cached = server.MEDIA_CACHE_DIR / "0123456789.png"
        cached.write_bytes(b"image-data")
        response = self.client.get("/media/0123456789.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"image-data")
        response.close()
        self.assertEqual(self.client.get("/media/../server.py").status_code, 404)

    def test_delete_history_removes_record_and_cached_media(self):
        server.MEDIA_CACHE_DIR.mkdir(parents=True)
        cached = server.MEDIA_CACHE_DIR / "0123456789.png"
        cached.write_bytes(b"image-data")
        with closing(server._history_connection()) as connection:
            connection.execute(
                """INSERT INTO generations
                   (job_id, created_at, profile_id, media_type, ratio, prompt,
                    status, filename, mime_type, size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("0123456789", "2026-11-04T00:00:00+00:00", "profile-one",
                 "image", "9:16", "A test image", "complete", cached.name,
                 "image/png", len(b"image-data")),
            )
            connection.commit()

        response = self.client.delete(
            "/api/history/0123456789",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(cached.exists())
        self.assertEqual(self.client.get("/api/history", headers=self.headers).get_json(), [])

    def test_delete_history_removes_unfinished_jobs(self):
        for status in ("running", "waiting_media"):
            with self.subTest(status=status):
                job = server.GenerateJob("profile-one", "image", "9:16")
                server._create_generation_record(job, "A pending image")
                if status == "waiting_media":
                    server._update_generation_record(job, status)

                response = self.client.delete(
                    f"/api/history/{job.id}",
                    headers=self.headers,
                )

                self.assertEqual(response.status_code, 200)
                self.assertNotIn(
                    job.id,
                    [item["id"] for item in self.client.get(
                        "/api/history",
                        headers=self.headers,
                    ).get_json()],
                )

    def test_deleted_history_does_not_leave_late_generated_media(self):
        job = server.GenerateJob("profile-one", "image", "9:16")
        server.MEDIA_CACHE_DIR.mkdir(parents=True)
        cached = server.MEDIA_CACHE_DIR / f"{job.id}.png"
        cached.write_bytes(b"image-data")

        server._update_generation_record(
            job,
            "complete",
            filename=cached.name,
            mime_type="image/png",
            size_bytes=len(b"image-data"),
        )

        self.assertFalse(cached.exists())

    def test_server_open_uses_one_time_pairing_page(self):
        with (
            patch("server.GENERATE_BRIDGE.issue_pairing", return_value="one_time_ticket"),
            patch("server._launch", return_value={"ok": True}) as launch,
        ):
            response = self.client.post(
                "/api/profiles/profile-one/open",
                json={},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], ccp.open_profile)
        self.assertIs(launch.call_args.args[-1], False)
        self.assertEqual(launch.call_args.args[5], "http://127.0.0.1:8000/pair#one_time_ticket")
        self.assertEqual(len(launch.call_args.args), 7)

    def test_server_normal_open_does_not_set_a_destination_or_pair_extension(self):
        with (
            patch("server.GENERATE_BRIDGE.issue_pairing") as issue_pairing,
            patch("server._launch", return_value={"ok": True}) as launch,
        ):
            response = self.client.post(
                "/api/profiles/profile-one/open",
                json={"normal": True},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        issue_pairing.assert_not_called()
        self.assertEqual(launch.call_args.args[0].title, "Buka Chrome profile-one")
        self.assertIsNone(launch.call_args.args[5])

    def test_server_clear_profile_starts_clear_job(self):
        with patch("server._launch", return_value=server.app.response_class(
            response='{"id":"clear-job"}',
            status=200,
            mimetype="application/json",
        )) as launch:
            response = self.client.post(
                "/api/profiles/profile-one/clear",
                json={},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIs(launch.call_args.args[1], server._clear_profile_job)
        self.assertEqual(launch.call_args.args[2], "profile-one")

    def test_clear_profile_removes_browser_data_and_preserves_extensions(self):
        profile_dir = self.profiles_dir / "profile-one"
        default_dir = profile_dir / "Default"
        extension_dir = default_dir / "Extensions" / "abcdefghijklmnop" / "1.0"
        extension_dir.mkdir(parents=True)
        (extension_dir / "manifest.json").write_text("{}", encoding="utf-8")
        (default_dir / "Local Extension Settings" / "abcdefghijklmnop").mkdir(parents=True)
        (default_dir / "Local Extension Settings" / "abcdefghijklmnop" / "state").write_text(
            "extension-data",
            encoding="utf-8",
        )
        (default_dir / "IndexedDB" / "chrome-extension_abcdefghijklmnop_0.indexeddb.leveldb").mkdir(
            parents=True,
        )
        (default_dir / "IndexedDB" / "https_gemini.google.com_0.indexeddb.leveldb").mkdir()
        (default_dir / "Preferences").write_text('{"extensions":{}}', encoding="utf-8")
        (default_dir / "Secure Preferences").write_text('{"extensions":{}}', encoding="utf-8")
        (default_dir / "Cookies").write_bytes(b"cookie-data")
        (default_dir / "History").write_bytes(b"history-data")
        (profile_dir / "Local State").write_text('{"extensions":{}}', encoding="utf-8")
        (profile_dir / "python-mode-email.txt").write_text("user@example.com", encoding="utf-8")

        with patch("create_chrome_profile.find_running_profile_processes", return_value={}):
            result = ccp.clear_profile(self.profiles_dir, "profile-one")

        self.assertEqual(result, 0)
        self.assertTrue(profile_dir.is_dir())
        self.assertTrue((profile_dir / "Local State").is_file())
        self.assertTrue((default_dir / "Preferences").is_file())
        self.assertTrue((default_dir / "Secure Preferences").is_file())
        self.assertTrue((extension_dir / "manifest.json").is_file())
        self.assertEqual(
            (default_dir / "Local Extension Settings" / "abcdefghijklmnop" / "state")
            .read_text(encoding="utf-8"),
            "extension-data",
        )
        self.assertTrue(
            (default_dir / "IndexedDB" / "chrome-extension_abcdefghijklmnop_0.indexeddb.leveldb")
            .is_dir()
        )
        self.assertFalse((default_dir / "Cookies").exists())
        self.assertFalse((default_dir / "History").exists())
        self.assertFalse((default_dir / "IndexedDB" / "https_gemini.google.com_0.indexeddb.leveldb").exists())
        self.assertFalse((profile_dir / "python-mode-email.txt").exists())

    def test_clear_profile_refuses_open_profile(self):
        profile_dir = self.profiles_dir / "profile-one"
        sentinel = profile_dir / "keep-me"
        sentinel.write_text("data", encoding="utf-8")

        with patch(
            "create_chrome_profile.find_running_profile_processes",
            return_value={"profile-one": [123]},
        ):
            result = ccp.clear_profile(self.profiles_dir, "profile-one")

        self.assertEqual(result, 1)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "data")

    def test_pairing_page_does_not_echo_ticket_or_cache(self):
        response = self.client.get("/pair#one_time_ticket")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(b"one_time_ticket", response.data)


if __name__ == "__main__":
    unittest.main()
