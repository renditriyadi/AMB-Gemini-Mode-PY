from contextlib import closing
import tempfile
import time
import unittest
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

    def test_pairing_page_does_not_echo_ticket_or_cache(self):
        response = self.client.get("/pair#one_time_ticket")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(b"one_time_ticket", response.data)


if __name__ == "__main__":
    unittest.main()
