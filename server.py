#!/usr/bin/env python3
"""Python Mode UI server.

Membungkus fungsi yang sudah ada (create_chrome_profile.py dan gemini_prompt_bridge.py)
tanpa mengubah isinya. Caranya: tiap aksi dijalankan di thread sendiri, lalu
print/input/getpass milik fungsi lama dialihkan ke job log dan form di UI.

Jalankan:  python server.py   ->  buka http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import builtins
import contextvars
from contextlib import closing
import getpass
import mimetypes
import queue
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from datetime import datetime, timezone

from flask import Flask, abort, jsonify, request, send_from_directory

import create_chrome_profile as ccp
import gemini_generate_bridge as generate_bridge

HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(24)
MAX_PROMPT = 5000
EXTENSION_CONNECT_TIMEOUT = 45
PROFILE_EMAIL_FILE = "python-mode-email.txt"
MEDIA_CACHE_DIR = HERE / "media-cache"
HISTORY_DB_PATH = MEDIA_CACHE_DIR / "history.sqlite3"
MAX_MEDIA_BYTES = 500 * 1024 * 1024
GENERATE_BRIDGE = generate_bridge.GenerateBridge()


def _history_connection() -> sqlite3.Connection:
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(HISTORY_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            job_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            chat_id TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL,
            ratio TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0
        )
    """)
    connection.commit()
    return connection


def _create_generation_record(job: "GenerateJob", prompt: str) -> None:
    with closing(_history_connection()) as connection:
        connection.execute(
            """INSERT INTO generations
               (job_id, created_at, profile_id, media_type, ratio, prompt, status)
               VALUES (?, ?, ?, ?, ?, ?, 'running')""",
            (
                job.id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                job.profile,
                job.media_type,
                job.ratio,
                prompt,
            ),
        )
        connection.commit()


def _update_generation_record(
    job: "GenerateJob",
    status: str,
    *,
    error: str = "",
    chat_id: str = "",
    filename: str = "",
    mime_type: str = "",
    size_bytes: int = 0,
) -> None:
    with closing(_history_connection()) as connection:
        cursor = connection.execute(
            """UPDATE generations SET status=?, error=?, chat_id=?, filename=?,
               mime_type=?, size_bytes=? WHERE job_id=?""",
            (status, error, chat_id, filename, mime_type, size_bytes, job.id),
        )
        connection.commit()
    if cursor.rowcount == 0 and filename:
        if not re.fullmatch(rf"{job.id}\.[a-z0-9]{{2,5}}", filename):
            app.logger.error("Invalid generated media filename for deleted history %s", job.id)
            return
        try:
            (MEDIA_CACHE_DIR / filename).unlink(missing_ok=True)
        except OSError:
            app.logger.exception("Failed to remove generated media for deleted history %s", job.id)


def _save_generated_media(job: "GenerateJob", result: dict[str, Any]) -> dict[str, Any]:
    downloaded_path = result.get("download_path")
    if not isinstance(downloaded_path, str) or not downloaded_path:
        raise RuntimeError("Extension tidak memberikan lokasi file hasil unduhan Gemini.")
    source = Path(downloaded_path).resolve()
    downloads_dir = (Path.home() / "Downloads").resolve()
    if not source.is_relative_to(downloads_dir):
        raise RuntimeError("Lokasi unduhan di luar folder Downloads standar; file tidak dipindahkan.")
    if not source.is_file():
        raise RuntimeError("File hasil unduhan Gemini tidak ditemukan.")
    size_bytes = source.stat().st_size
    if size_bytes <= 0 or size_bytes > MAX_MEDIA_BYTES:
        raise RuntimeError("Ukuran file hasil tidak valid atau melebihi 500 MB.")

    extension = source.suffix.lower()
    mime_type = result.get("mime_type") or mimetypes.guess_type(source.name)[0] or ""
    if extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".mp4", ".webm", ".mov", ".m4v"}:
        extension = mimetypes.guess_extension(mime_type) or ""
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".mp4", ".webm", ".mov", ".m4v"}
    if extension not in allowed_extensions:
        raise RuntimeError("Format file hasil Gemini tidak didukung.")
    if job.media_type == "image" and extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}:
        raise RuntimeError("Gemini mengunduh format video, padahal jenis yang diminta adalah gambar.")
    if job.media_type == "video" and extension not in {".mp4", ".webm", ".mov", ".m4v"}:
        raise RuntimeError("Gemini mengunduh format gambar, padahal jenis yang diminta adalah video.")

    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{job.id}{extension}"
    destination = MEDIA_CACHE_DIR / filename
    shutil.copy2(source, destination)
    return {
        "filename": filename,
        "mime_type": mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "size_bytes": size_bytes,
    }

# --------------------------------------------------------------------------
# Pengalihan print / input / getpass per job (pakai contextvar, jadi ikut
# terbawa ke asyncio.to_thread yang dipakai bridge).
# --------------------------------------------------------------------------
_current: contextvars.ContextVar[Optional["Job"]] = contextvars.ContextVar("job", default=None)
_real_input = builtins.input
_real_getpass = getpass.getpass


def _input(prompt: Any = "") -> str:
    job = _current.get()
    return job.ask(str(prompt), secret=False) if job else _real_input(prompt)


def _getpass(prompt: str = "Password: ", stream: Any = None) -> str:
    job = _current.get()
    return job.ask(str(prompt), secret=True) if job else _real_getpass(prompt, stream)


class _Router:
    def __init__(self, real: Any) -> None:
        self._real = real

    def write(self, text: str) -> int:
        job = _current.get()
        if job:
            job.write(text)
            return len(text)
        return self._real.write(text)

    def flush(self) -> None:
        if not _current.get():
            self._real.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


builtins.input = _input
getpass.getpass = _getpass
sys.stdout = _Router(sys.stdout)
sys.stderr = _Router(sys.stderr)


class Cancelled(KeyboardInterrupt):
    pass


class Job:
    def __init__(self, title: str, profile: Optional[str] = None, presets: tuple = ()) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.profile = profile
        self.presets = list(presets)  # [(regex, value)], dipakai sekali lalu dibuang
        self.status = "running"  # running | waiting | done | failed
        self.code: Optional[int] = None
        self.lines: list[str] = []
        self.prompt: Optional[dict] = None
        self.started = time.time()
        self.cancelled = False
        self._buf = ""
        self._answers: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()

    # --- output
    def write(self, text: str) -> None:
        with self._lock:
            self._buf += text.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._add(line)

    def _add(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > 2000:
            del self.lines[:500]
        self.on_line(line)

    def on_line(self, line: str) -> None:  # hook untuk subclass
        pass

    # --- input
    def ask(self, prompt: str, secret: bool) -> str:
        for item in list(self.presets):
            if re.search(item[0], prompt):
                self.presets.remove(item)
                return item[1]
        if self.cancelled:
            raise Cancelled()
        self.prompt = {"text": prompt, "secret": secret, "enter_only": "Tekan Enter" in prompt}
        self.status = "waiting"
        try:
            while True:
                try:
                    return self._answers.get(timeout=0.5)
                except queue.Empty:
                    if self.cancelled:
                        raise Cancelled()
        finally:
            self.prompt = None
            if self.status == "waiting":
                self.status = "running"

    def answer(self, value: str) -> bool:
        if not self.prompt:
            return False
        self._answers.put(value)
        return True

    def finish(self, code: int) -> None:
        with self._lock:
            if self._buf:
                self._add(self._buf)
                self._buf = ""
        self.code = code
        self.status = "done" if code == 0 else "failed"
        self.presets.clear()

    @property
    def active(self) -> bool:
        return self.status in ("running", "waiting")

    def view(self, since: int = 0) -> dict:
        return {
            "id": self.id, "title": self.title, "profile": self.profile,
            "status": self.status, "code": self.code, "prompt": self.prompt,
            "lines": self.lines[since:], "next": len(self.lines),
        }


class GenerateJob(Job):
    def __init__(self, profile_id: str, media_type: str, ratio: str) -> None:
        label = "Gambar" if media_type == "image" else "Video"
        super().__init__(f"Generate {label} · {profile_id}", profile_id)
        self.media_type = media_type
        self.ratio = ratio
        self.chat_id = ""


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def start_job(job: Job, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
    with JOBS_LOCK:
        if job.profile and any(j.active and j.profile == job.profile for j in JOBS.values()):
            raise RuntimeError(f"Profil '{job.profile}' masih punya proses yang berjalan.")
        JOBS[job.id] = job
        for old in sorted(JOBS.values(), key=lambda j: j.started)[:-30]:
            if not old.active:
                JOBS.pop(old.id, None)

    def target() -> None:
        _current.set(job)
        try:
            result = fn(*args, **kwargs)
            code = result if isinstance(result, int) else 0
            job.finish(code)
            if isinstance(job, GenerateJob) and code != 0:
                _update_generation_record(
                    job,
                    "failed",
                    error=next((line for line in reversed(job.lines) if line.startswith("Info: ")), "Generate gagal."),
                    chat_id=job.chat_id,
                )
        except Cancelled:
            job.write("Dibatalkan.\n")
            job.finish(130)
            if isinstance(job, GenerateJob):
                _update_generation_record(job, "failed", error="Dibatalkan.", chat_id=job.chat_id)
        except generate_bridge.GenerateBridgeCancelled:
            job.write("Dibatalkan.\n")
            job.finish(130)
            if isinstance(job, GenerateJob):
                _update_generation_record(job, "failed", error="Dibatalkan.", chat_id=job.chat_id)
        except BaseException as exc:  # noqa: BLE001
            job.write(f"Error: {type(exc).__name__}: {exc}\n")
            job.finish(1)
            if isinstance(job, GenerateJob):
                _update_generation_record(
                    job,
                    "failed",
                    error=str(exc),
                    chat_id=job.chat_id,
                )

    threading.Thread(target=target, daemon=True).start()
    return job


# --------------------------------------------------------------------------
# Bridge: run_bridge() asli dijalankan apa adanya. Dua input() miliknya
# (ID profil dan prompt) disuplai dari antrean perintah UI.
# --------------------------------------------------------------------------
class BridgeJob(Job):
    def __init__(self) -> None:
        super().__init__("Prompt Bridge")
        self.cmds: "queue.Queue[Optional[dict]]" = queue.Queue()
        self.connected: set[str] = set()
        self.history: list[dict] = []
        self.current: Optional[dict] = None
        self._mark = 0

    def on_line(self, line: str) -> None:
        m = re.match(r"Extension terhubung untuk profil: (.+)$", line)
        if m:
            self.connected.add(m.group(1))
        m = re.match(r"Extension terputus dari profil: (.+)$", line)
        if m:
            self.connected.discard(m.group(1))

    def _finish_current(self) -> None:
        cmd = self.current
        if not cmd:
            return
        out = [x for x in self.lines[self._mark:] if x.strip()]
        status = next((x.split(":", 1)[1].strip() for x in out if x.startswith("Status:")), None)
        cmd["status"] = status or "gagal"
        cmd["detail"] = " | ".join(out)
        self.current = None

    def ask(self, prompt: str, secret: bool) -> str:  # type: ignore[override]
        if "ID profil target" in prompt:
            self._finish_current()
            cmd = self.cmds.get()  # blok sampai ada perintah, None = berhenti
            if cmd is None:
                return "0"
            cmd["status"] = "mengirim"
            self.current = cmd
            self._mark = len(self.lines)
            return cmd["profile_id"]
        if "Prompt Gemini" in prompt and self.current:
            return self.current["prompt"]
        return super().ask(prompt, secret)

    def send(self, profile_id: str, prompt: str) -> dict:
        cmd = {"id": uuid.uuid4().hex[:8], "profile_id": profile_id, "prompt": prompt,
               "status": "antre", "detail": "", "at": time.time()}
        self.history.append(cmd)
        del self.history[:-50]
        self.cmds.put(cmd)
        return cmd

    def stop(self) -> None:
        self.cmds.put(None)


BRIDGE: Optional[BridgeJob] = None


def bridge_runner() -> int:
    import gemini_prompt_bridge as gpb  # impor di sini: butuh paket 'websockets'
    try:
        asyncio.run(gpb.run_bridge())
    except OSError as exc:
        print(f"Bridge gagal start (port {gpb.PORT} dipakai?): {exc}")
        return 1
    return 0


# --------------------------------------------------------------------------
# Flask
# --------------------------------------------------------------------------
app = Flask(__name__)
ARGS: argparse.Namespace


@app.before_request
def guard() -> None:
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.path.startswith("/api/") and request.headers.get("X-UI-Token") != TOKEN:
        abort(403)


def err(message: str, code: int = 400):
    return jsonify({"error": message}), code


@app.get("/")
def index():
    html = (HERE / "ui" / "index.html").read_text(encoding="utf-8")
    return html.replace("__TOKEN__", TOKEN), 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


@app.get("/style.css")
def ui_stylesheet():
    return send_from_directory(HERE / "ui", "style.css")


@app.get("/pair")
def extension_pair_page():
    return generate_bridge.pair_page_html(), 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
    }


_run_cache: dict[str, Any] = {"at": 0.0, "value": None, "error": None}


def running_profiles(force_refresh: bool = False) -> tuple[Optional[dict], Optional[str]]:
    if force_refresh or time.time() - _run_cache["at"] > 3:
        try:
            _run_cache.update(value=ccp.find_running_profile_processes(ARGS.profiles_dir), error=None)
        except Exception as exc:  # noqa: BLE001
            _run_cache.update(value=None, error=str(exc))
        _run_cache["at"] = time.time()
    return _run_cache["value"], _run_cache["error"]


def bridge_view(since_cmds: bool = True) -> dict:
    b = BRIDGE
    alive = bool(b and b.active)
    return {
        "running": alive,
        "connected": sorted(b.connected) if b else [],
        "history": list(reversed(b.history)) if b else [],
        "job": b.id if b else None,
        "lines": b.lines[-30:] if b else [],
    }


def _profile_email(profile_dir: Path) -> str:
    try:
        return (profile_dir / PROFILE_EMAIL_FILE).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _save_profile_email(profiles_dir: Path, profile_id: str, email: str) -> None:
    profile_dir = ccp.get_profile_dir(profiles_dir, profile_id)
    if ARGS.dry_run:
        return
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"Folder profil '{profile_id}' tidak ditemukan.")
    email_path = profile_dir / PROFILE_EMAIL_FILE
    temporary_path = email_path.with_name(f"{email_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(email.strip(), encoding="utf-8")
        temporary_path.replace(email_path)
    finally:
        temporary_path.unlink(missing_ok=True)


@app.get("/api/state")
def state():
    running, run_error = running_profiles()
    profiles = [
        {"id": p.name, "pids": (running or {}).get(p.name, []),
         "busy": any(j.active and j.profile == p.name for j in JOBS.values()),
         **GENERATE_BRIDGE.status(p.name),
         "email": _profile_email(p)}
        for p in ccp.list_profiles(ARGS.profiles_dir)
    ]
    chrome = ARGS.chrome or ccp.find_chrome()
    return jsonify({
        "profiles": profiles, "status_error": run_error,
        "chrome": str(chrome) if chrome else None,
        "profiles_dir": str(ARGS.profiles_dir), "dry_run": ARGS.dry_run,
        "jobs": [{"id": j.id, "title": j.title, "status": j.status} for j in sorted(JOBS.values(), key=lambda j: -j.started)][:8],
        "bridge": bridge_view(),
    })


def _profile_id(data: dict) -> str:
    pid = str(data.get("id", "")).strip()
    if not ccp.is_valid_profile_id(pid):
        raise ValueError("ID profil tidak valid (huruf/angka, spasi . _ - , maks 64, tidak boleh nama khusus Windows).")
    return pid


def _launch(job: Job, fn, *a, **kw):
    try:
        return jsonify(start_job(job, fn, *a, **kw).view())
    except RuntimeError as exc:
        return err(str(exc), 409)


@app.post("/api/profiles")
def api_create():
    try:
        pid = _profile_id(request.get_json(force=True))
    except ValueError as exc:
        return err(str(exc))
    return _launch(Job(f"Buat profil {pid}", pid), ccp.create_profile, ARGS.profiles_dir, pid)


@app.post("/api/profiles/<pid>/open")
def api_open(pid: str):
    if not ccp.is_valid_profile_id(pid):
        return err("ID profil tidak valid.")
    url = (request.get_json(silent=True) or {}).get("url", ccp.DEFAULT_URL) or None
    if url and not re.match(r"^https?://", url):
        return err("URL harus diawali http:// atau https://")
    if not ARGS.dry_run:
        ticket = GENERATE_BRIDGE.issue_pairing(pid)
        url = f"http://127.0.0.1:{ARGS.port}/pair#{ticket}"
    return _launch(
        Job(f"Buka profil {pid}", pid),
        ccp.open_profile,
        ARGS.chrome,
        ARGS.profiles_dir,
        pid,
        url,
        ARGS.dry_run,
    )


def _generate_extension_job(job: GenerateJob, profile_id: str, prompt: str) -> int:
    def check_cancelled() -> None:
        if job.cancelled:
            raise generate_bridge.GenerateBridgeCancelled()

    def on_prompt_sent(result: dict[str, Any]) -> None:
        job.chat_id = result.get("chat_id", "")
        job.write("Status extension: prompt_sent\n")
        if result.get("detail"):
            job.write(f"Info: {result['detail']}\n")
        job.write("Menunggu hasil Gemini dan unduhan media...\n")
        _update_generation_record(
            job,
            "waiting_media",
            chat_id=job.chat_id,
        )

    if ARGS.dry_run:
        raise RuntimeError("Mode uji tidak dapat membuka profil atau mengirim prompt ke Gemini.")

    if not GENERATE_BRIDGE.status(profile_id)["extension_ready"]:
        running, run_error = running_profiles(force_refresh=True)
        if run_error:
            raise RuntimeError(f"Status Chrome tidak bisa dibaca: {run_error}")
        if not (running or {}).get(profile_id):
            check_cancelled()
            ticket = GENERATE_BRIDGE.issue_pairing(profile_id)
            pairing_url = f"http://127.0.0.1:{ARGS.port}/pair#{ticket}"
            job.write(f"Membuka profil Chrome '{profile_id}'...\n")
            result = ccp.open_profile(
                ARGS.chrome,
                ARGS.profiles_dir,
                profile_id,
                pairing_url,
                ARGS.dry_run,
            )
            if result != 0:
                raise RuntimeError(f"Gagal membuka profil Chrome '{profile_id}'.")
        else:
            job.write(f"Menunggu extension terhubung pada profil '{profile_id}'...\n")

        deadline = time.monotonic() + EXTENSION_CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            check_cancelled()
            if GENERATE_BRIDGE.status(profile_id)["extension_ready"]:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(
                f"Extension pada profil '{profile_id}' belum terhubung. "
                "Pastikan extension AMB sudah dipasang pada profil tersebut."
            )

    check_cancelled()
    result = GENERATE_BRIDGE.send_prompt(
        profile_id,
        prompt,
        check_cancelled,
        media_type=job.media_type,
        ratio=job.ratio,
        on_prompt_sent=on_prompt_sent,
        timeout=900,
    )
    detail = result.get("detail")
    status = result.get("status", "unknown")
    if status != "complete":
        job.write(f"Status extension: {status}\n")
        if detail:
            job.write(f"Info: {detail}\n")
    job.chat_id = result.get("chat_id", job.chat_id)
    if status != "complete":
        return 1

    media = _save_generated_media(job, result)
    _update_generation_record(
        job,
        "complete",
        chat_id=job.chat_id,
        filename=media["filename"],
        mime_type=media["mime_type"],
        size_bytes=media["size_bytes"],
    )
    job.write(f"Hasil tersimpan: {MEDIA_CACHE_DIR / media['filename']}\n")
    return 0


@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("Format permintaan tidak valid.")
    pid, prompt = data.get("profile_id"), data.get("prompt")
    media_type, ratio = data.get("media_type", "video"), data.get("ratio", "9:16")
    if not isinstance(pid, str) or not isinstance(prompt, str):
        return err("ID profil dan prompt harus berupa teks.")
    if media_type not in ("video", "image"):
        return err("Jenis media harus video atau image.")
    if ratio not in ("9:16", "16:9"):
        return err("Rasio harus 9:16 atau 16:9.")
    pid, prompt = pid.strip(), prompt.strip()
    if not ccp.is_valid_profile_id(pid):
        return err("ID profil tidak valid.")
    if not prompt:
        return err("Prompt kosong.")
    if len(prompt) > MAX_PROMPT:
        return err(f"Prompt maksimal {MAX_PROMPT} karakter.")
    profile_dir = ccp.get_profile_dir(ARGS.profiles_dir, pid)
    if not profile_dir.is_dir():
        return err("Folder profil tidak ditemukan.", 404)
    job = GenerateJob(pid, media_type, ratio)
    _create_generation_record(job, prompt)
    response = _launch(
        job,
        _generate_extension_job,
        job,
        pid,
        prompt,
    )
    if response.status_code != 200:
        with closing(_history_connection()) as connection:
            connection.execute("DELETE FROM generations WHERE job_id=?", (job.id,))
            connection.commit()
    return response


@app.get("/api/history")
def api_history():
    with closing(_history_connection()) as connection:
        rows = connection.execute(
            """SELECT job_id, created_at, profile_id, chat_id, media_type, ratio,
                      prompt, status, error, filename, mime_type, size_bytes
               FROM generations ORDER BY created_at DESC, rowid DESC"""
        ).fetchall()
    return jsonify([{
        "id": row["job_id"],
        "created_at": row["created_at"],
        "profile_id": row["profile_id"],
        "chat_id": row["chat_id"],
        "media_type": row["media_type"],
        "ratio": row["ratio"],
        "prompt": row["prompt"],
        "status": row["status"],
        "error": row["error"],
        "media_url": f"/media/{row['filename']}" if row["filename"] else "",
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
    } for row in rows])


@app.delete("/api/history/<job_id>")
def api_delete_history(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{10}", job_id):
        return err("ID history tidak valid.", 404)

    with closing(_history_connection()) as connection:
        row = connection.execute(
            "SELECT status, filename FROM generations WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            return err("Hasil generate tidak ditemukan.", 404)
        filename = row["filename"]
        if filename:
            if not re.fullmatch(rf"{job_id}\.[a-z0-9]{{2,5}}", filename):
                return err("Nama file history tidak valid.", 500)
            media_path = MEDIA_CACHE_DIR / filename
            try:
                media_path.unlink(missing_ok=True)
            except OSError:
                app.logger.exception("Failed to remove generated media for history %s", job_id)
                return err("File media gagal dihapus. History tidak diubah.", 500)

        connection.execute("DELETE FROM generations WHERE job_id=?", (job_id,))
        connection.commit()
    return jsonify({"ok": True})


@app.get("/media/<path:filename>")
def media_file(filename: str):
    if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{10}\.[a-z0-9]{2,5}", filename):
        return err("Nama file media tidak valid.", 404)
    return send_from_directory(MEDIA_CACHE_DIR, filename, conditional=True)


def _close_profile_job(profile_id: str) -> int:
    result = ccp.close_profile(ARGS.profiles_dir, profile_id, ARGS.dry_run)
    if result == 0 and not ARGS.dry_run:
        GENERATE_BRIDGE.revoke(profile_id)
    return result


@app.post("/api/profiles/<pid>/close")
def api_close(pid: str):
    return _launch(Job(f"Tutup profil {pid}", pid), _close_profile_job, pid)


def _creds(data: dict) -> tuple:
    email, password = str(data.get("email", "")).strip(), str(data.get("password", ""))
    if not email or not password:
        raise ValueError("Email dan password Google wajib diisi (hanya disimpan di memori selama proses).")
    return ((r"^Email Google", email), (r"^Password Google", password))


@app.post("/api/profiles/<pid>/grab")
def api_grab(pid: str):
    data = request.get_json(force=True)
    try:
        presets = _creds(data)
    except ValueError as exc:
        return err(str(exc))
    email = presets[0][1]
    return _launch(Job(f"Grab {pid}", pid, presets), _grab_with_email, pid, email)


def _grab_with_email(pid: str, email: str) -> int:
    _save_profile_email(ARGS.profiles_dir, pid, email)
    return ccp.grab_profile(ARGS.profiles_dir, pid, ARGS.chrome, ARGS.dry_run)


def _new_and_grab(pid: str, email: str) -> int:
    # sama seperti menu Grab opsi 2: buat profil lalu Grab dengan force_login
    if ccp.get_profile_dir(ARGS.profiles_dir, pid).exists():
        print(f"Profil '{pid}' sudah ada. Pakai Grab pada profil itu.")
        return 1
    if ARGS.dry_run:
        print(f"Mode uji: profil baru '{pid}' akan dibuat.")
    else:
        ccp.create_profile(ARGS.profiles_dir, pid)
    _save_profile_email(ARGS.profiles_dir, pid, email)
    return ccp.grab_profile(ARGS.profiles_dir, pid, ARGS.chrome, ARGS.dry_run, force_login=True)


@app.post("/api/profiles/new-grab")
def api_new_grab():
    data = request.get_json(force=True)
    try:
        pid, presets = _profile_id(data), _creds(data)
    except ValueError as exc:
        return err(str(exc))
    email = presets[0][1]
    return _launch(Job(f"Profil baru + Grab {pid}", pid, presets), _new_and_grab, pid, email)


@app.post("/api/profiles/delete")
def api_delete():
    data = request.get_json(force=True)
    every = bool(data.get("all"))
    wanted = set(data.get("ids") or [])
    profiles = [p for p in ccp.list_profiles(ARGS.profiles_dir) if every or p.name in wanted]
    if not profiles:
        return err("Tidak ada profil yang dipilih.")
    title = "Hapus semua profil" if every else f"Hapus {', '.join(p.name for p in profiles)}"
    job = Job(title, profiles[0].name if len(profiles) == 1 else None, ((r"^Yakin hapus", "y"),))
    return _launch(job, ccp.delete_profiles, ARGS.profiles_dir, profiles, ARGS.dry_run, every)


@app.get("/api/jobs/<jid>")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        return err("Job tidak ditemukan.", 404)
    return jsonify(job.view(int(request.args.get("since", 0))))


@app.post("/api/jobs/<jid>/answer")
def api_answer(jid: str):
    job = JOBS.get(jid)
    if not job or not job.answer(str((request.get_json(force=True) or {}).get("value", ""))):
        return err("Tidak ada pertanyaan yang menunggu.", 409)
    return jsonify({"ok": True})


@app.post("/api/jobs/<jid>/cancel")
def api_cancel(jid: str):
    job = JOBS.get(jid)
    if not job:
        return err("Job tidak ditemukan.", 404)
    job.cancelled = True
    if isinstance(job, BridgeJob):
        job.stop()
    return jsonify({"ok": True})


@app.get("/api/bridge")
def api_bridge():
    try:
        import gemini_prompt_bridge as gpb
        key, error = gpb.load_or_create_bridge_key(), None
        host, port = gpb.HOST, gpb.PORT
    except ImportError as exc:
        key, error, host, port = None, f"Paket belum terpasang: {exc.name}. Jalankan pip install -r requirements-server.txt", "127.0.0.1", 8765
    return jsonify({**bridge_view(), "key": key, "error": error, "address": f"ws://{host}:{port}"})


@app.post("/api/bridge/start")
def api_bridge_start():
    global BRIDGE
    if BRIDGE and BRIDGE.active:
        return err("Bridge sudah berjalan.", 409)
    BRIDGE = BridgeJob()
    return _launch(BRIDGE, bridge_runner)


@app.post("/api/bridge/stop")
def api_bridge_stop():
    if not (BRIDGE and BRIDGE.active):
        return err("Bridge tidak berjalan.", 409)
    BRIDGE.stop()
    return jsonify({"ok": True})


@app.post("/api/bridge/send")
def api_bridge_send():
    data = request.get_json(force=True)
    pid, prompt = str(data.get("profile_id", "")).strip(), str(data.get("prompt", "")).strip()
    if not (BRIDGE and BRIDGE.active):
        return err("Bridge belum dijalankan.", 409)
    try:
        import gemini_prompt_bridge as gpb
    except ImportError:
        return err("Paket websockets belum terpasang.", 500)
    if not gpb.valid_profile_id(pid):
        return err("ID profil tidak valid.")
    if pid not in BRIDGE.connected:
        return err("Profil belum terhubung. Buka extension di profil itu dan simpan pengaturannya.", 409)
    if not prompt:
        return err("Prompt kosong.")
    if len(prompt) > MAX_PROMPT:
        return err(f"Prompt maksimal {MAX_PROMPT} karakter.")
    return jsonify(BRIDGE.send(pid, prompt))


def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser(description="AMB Mode server (Chrome profiles + Gemini Generate extension).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profiles-dir", type=Path, default=ccp.DEFAULT_PROFILES_DIR)
    parser.add_argument("--chrome", type=Path, default=None, help="Lokasi chrome.exe jika tidak terdeteksi.")
    parser.add_argument("--dry-run", action="store_true", help="Aksi hanya disimulasikan (sama seperti --dry-run di CLI).")
    ARGS = parser.parse_args()
    url = f"http://127.0.0.1:{ARGS.port}"
    print(f"Python Mode UI aktif hanya di komputer ini: {url}")
    try:
        GENERATE_BRIDGE.start()
        print(f"Generate extension bridge aktif hanya di komputer ini (127.0.0.1:{GENERATE_BRIDGE.port}).")
    except RuntimeError as exc:
        print(f"PERINGATAN: {exc} Generate Video via extension tidak tersedia.")
    app.run(host="127.0.0.1", port=ARGS.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
