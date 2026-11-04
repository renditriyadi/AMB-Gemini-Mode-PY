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
import getpass
import queue
import re
import secrets
import sys
import threading
import time
import webbrowser
import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, abort, jsonify, request

import create_chrome_profile as ccp

HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(24)
MAX_PROMPT = 5000
PROFILE_EMAIL_FILE = "python-mode-email.txt"

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
            job.finish(result if isinstance(result, int) else 0)
        except Cancelled:
            job.write("Dibatalkan.\n")
            job.finish(130)
        except BaseException as exc:  # noqa: BLE001
            job.write(f"Error: {type(exc).__name__}: {exc}\n")
            job.finish(1)

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


_run_cache: dict[str, Any] = {"at": 0.0, "value": None, "error": None}


def running_profiles() -> tuple[Optional[dict], Optional[str]]:
    if time.time() - _run_cache["at"] > 3:
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
    url = (request.get_json(silent=True) or {}).get("url", ccp.DEFAULT_URL) or None
    if url and not re.match(r"^https?://", url):
        return err("URL harus diawali http:// atau https://")
    return _launch(Job(f"Buka profil {pid}", pid), ccp.open_profile, ARGS.chrome, ARGS.profiles_dir, pid, url, ARGS.dry_run)


@app.post("/api/profiles/<pid>/close")
def api_close(pid: str):
    return _launch(Job(f"Tutup profil {pid}", pid), ccp.close_profile, ARGS.profiles_dir, pid, ARGS.dry_run)


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
    parser = argparse.ArgumentParser(description="UI server untuk Python Mode (Chrome profile + Gemini prompt bridge).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profiles-dir", type=Path, default=ccp.DEFAULT_PROFILES_DIR)
    parser.add_argument("--chrome", type=Path, default=None, help="Lokasi chrome.exe jika tidak terdeteksi.")
    parser.add_argument("--dry-run", action="store_true", help="Aksi hanya disimulasikan (sama seperti --dry-run di CLI).")
    parser.add_argument("--no-browser", action="store_true")
    ARGS = parser.parse_args()
    url = f"http://127.0.0.1:{ARGS.port}"
    print(f"Python Mode UI aktif hanya di komputer ini: {url}")
    if not ARGS.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=ARGS.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
