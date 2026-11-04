"""Loopback-only bridge for the new, independently paired Gemini extension."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"
PORT = 8766
MAX_PROMPT_LENGTH = 5000
PAIRING_TTL_SECONDS = 120
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_EXTENSION_ID = re.compile(r"^[a-p]{32}$")


class GenerateBridgeError(RuntimeError):
    pass


class GenerateBridgeCancelled(Exception):
    pass


def valid_profile_id(profile_id: Any) -> bool:
    return isinstance(profile_id, str) and bool(_PROFILE_ID.fullmatch(profile_id))


def is_extension_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme == "chrome-extension"
        and bool(_EXTENSION_ID.fullmatch(parsed.netloc))
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


class GenerateBridge:
    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        if host != HOST:
            raise ValueError("Generate bridge hanya boleh bind ke 127.0.0.1.")
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self._tickets: dict[str, tuple[str, float]] = {}
        self._pair_keys: dict[str, str] = {}
        self._connections: dict[str, ServerConnection] = {}
        self._pending: dict[
            str,
            tuple[str, asyncio.Future[dict[str, Any]], asyncio.Future[dict[str, Any]]],
        ] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._startup_error: Optional[BaseException] = None
        self._server: Any = None

    def start(self, timeout: float = 5) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._thread_main, name="gemini-generate-bridge", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Generate extension bridge tidak siap setelah batas waktu.")
        if self._startup_error:
            raise RuntimeError(f"Generate extension bridge gagal start: {self._startup_error}") from self._startup_error

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve_forever())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            loop.close()

    async def _serve_forever(self) -> None:
        try:
            self._server = await serve(
                self._handle_client,
                self.host,
                self.port,
                max_size=32_768,
                ping_interval=20,
                ping_timeout=20,
            )
        except OSError as exc:
            self._startup_error = exc
            self._ready.set()
            return
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        self._stop_event = asyncio.Event()
        self._ready.set()
        try:
            await self._stop_event.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()

    def stop(self, timeout: float = 3) -> None:
        if not self._loop or not self._loop.is_running() or not self._stop_event:
            return
        self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout)

    def issue_pairing(self, profile_id: str) -> str:
        if not valid_profile_id(profile_id):
            raise ValueError("ID profil tidak valid.")
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._tickets = {
                ticket: value for ticket, value in self._tickets.items()
                if value[1] > now and value[0] != profile_id
            }
            self._tickets[token] = (profile_id, now + PAIRING_TTL_SECONDS)
        return token

    def status(self, profile_id: str) -> dict[str, bool]:
        now = time.monotonic()
        with self._lock:
            paired = profile_id in self._pair_keys
            connected = profile_id in self._connections
            pairing = any(pid == profile_id and expiry > now for pid, expiry in self._tickets.values())
        return {"extension_paired": paired, "extension_ready": connected, "pairing": pairing}

    def revoke(self, profile_id: str) -> None:
        with self._lock:
            self._pair_keys.pop(profile_id, None)
            self._tickets = {
                ticket: value for ticket, value in self._tickets.items()
                if value[0] != profile_id
            }
            websocket = self._connections.pop(profile_id, None)
        if websocket and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(websocket.close(code=1001, reason="Profile closed"), self._loop)

    async def _handle_client(self, websocket: ServerConnection) -> None:
        origin = websocket.request.headers.get("Origin", "")
        if not is_extension_origin(origin):
            await websocket.close(code=1008, reason="Chrome extension origin required")
            return

        profile_id: Optional[str] = None
        try:
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=10)
                message = self._decode(raw)
            except asyncio.TimeoutError:
                await websocket.close(code=1008, reason="Pairing timed out")
                return
            except ValueError:
                await websocket.close(code=1008, reason="Invalid bridge message")
                return
            if message.get("type") == "pair":
                ticket = message.get("ticket")
                if not isinstance(ticket, str):
                    await websocket.close(code=1008, reason="Invalid pairing ticket")
                    return
                with self._lock:
                    ticket_data = self._tickets.pop(ticket, None)
                    if not ticket_data or ticket_data[1] <= time.monotonic():
                        ticket_data = None
                    if ticket_data:
                        profile_id = ticket_data[0]
                        pairing_key = secrets.token_urlsafe(32)
                        self._pair_keys[profile_id] = pairing_key
                        old_connection = self._connections.get(profile_id)
                        self._connections[profile_id] = websocket
                    else:
                        pairing_key = ""
                        old_connection = None
                if not profile_id:
                    await websocket.close(code=1008, reason="Pairing ticket expired or invalid")
                    return
                if old_connection and old_connection is not websocket:
                    await old_connection.close(code=4001, reason="Profile was paired again")
                await websocket.send(json.dumps({
                    "type": "paired",
                    "profileId": profile_id,
                    "pairingKey": pairing_key,
                }))
            elif message.get("type") == "hello":
                requested_profile = message.get("profileId")
                supplied_key = message.get("pairingKey")
                with self._lock:
                    expected = self._pair_keys.get(requested_profile) if isinstance(requested_profile, str) else None
                    old_connection = self._connections.get(requested_profile) if expected else None
                    if expected and isinstance(supplied_key, str) and secrets.compare_digest(expected, supplied_key):
                        profile_id = requested_profile
                        self._connections[profile_id] = websocket
                if not profile_id:
                    await websocket.close(code=1008, reason="Pairing required")
                    return
                if old_connection and old_connection is not websocket:
                    await old_connection.close(code=4001, reason="Extension reconnected")
                await websocket.send(json.dumps({"type": "hello_ack", "profileId": profile_id}))
            else:
                await websocket.close(code=1008, reason="Pairing or hello required")
                return

            async for raw_message in websocket:
                try:
                    message = self._decode(raw_message)
                except ValueError:
                    continue
                if message.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                    continue
                if message.get("type") in ("command_result", "media_result"):
                    request_id = message.get("requestId")
                    pending = self._pending.get(request_id) if isinstance(request_id, str) else None
                    if pending and pending[0] == profile_id:
                        result_profile = message.get("profileId")
                        if result_profile == profile_id:
                            status = message.get("status")
                            detail = message.get("detail", "")
                            if not isinstance(status, str) or status == "unknown":
                                status = "error"
                                if not isinstance(detail, str) or not detail:
                                    detail = (
                                        "Extension tidak mengembalikan status. "
                                        "Muat ulang extension dan refresh tab Gemini."
                                    )
                            result = {
                                "status": status if len(status) <= 64 else "error",
                                "detail": detail[:1000] if isinstance(detail, str) else "",
                                "chat_id": (
                                    message["chatId"]
                                    if isinstance(message.get("chatId"), str)
                                    and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message["chatId"])
                                    else ""
                                ),
                            }
                            if message.get("type") == "command_result":
                                if not pending[1].done():
                                    pending[1].set_result(result)
                                if result["status"] != "prompt_sent" and not pending[2].done():
                                    pending[2].set_result(result)
                            elif not pending[2].done():
                                for source, target in (
                                    ("downloadPath", "download_path"),
                                    ("mimeType", "mime_type"),
                                ):
                                    value = message.get(source)
                                    if isinstance(value, str):
                                        result[target] = value[:2048]
                                pending[2].set_result(result)
        except ConnectionClosed:
            pass
        except OSError as exc:
            print(f"Generate extension connection ended: {type(exc).__name__}")
        finally:
            if profile_id:
                with self._lock:
                    if self._connections.get(profile_id) is websocket:
                        self._connections.pop(profile_id, None)
                for pending_profile, *futures in self._pending.values():
                    if pending_profile == profile_id:
                        for future in futures:
                            if not future.done():
                                future.set_exception(GenerateBridgeError("Extension terputus saat job berjalan."))

    @staticmethod
    def _decode(raw_message: Any) -> dict[str, Any]:
        if not isinstance(raw_message, str) or len(raw_message) > 16_384:
            raise ValueError("Invalid bridge message size.")
        message = json.loads(raw_message)
        if not isinstance(message, dict):
            raise ValueError("Bridge message must be an object.")
        return message

    async def _send_prompt(
        self,
        profile_id: str,
        prompt: str,
        media_type: str,
        ratio: str,
        check_cancelled: Callable[[], None],
        on_prompt_sent: Optional[Callable[[dict[str, Any]], None]],
        timeout: float,
    ) -> dict[str, Any]:
        with self._lock:
            websocket = self._connections.get(profile_id)
        if not websocket:
            raise GenerateBridgeError("Extension untuk profil ini belum terhubung. Buka ulang profil lewat AMB.")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        prompt_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        media_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = (profile_id, prompt_future, media_future)
        try:
            await websocket.send(json.dumps({
                "type": "generate_prompt",
                "requestId": request_id,
                "profileId": profile_id,
                "prompt": prompt,
                "mediaType": media_type,
                "ratio": ratio,
            }, ensure_ascii=False))
            deadline = time.monotonic() + timeout
            while not prompt_future.done():
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GenerateBridgeError("Extension tidak mengonfirmasi pengiriman prompt.")
                await asyncio.wait({prompt_future}, timeout=min(remaining, 0.25))
            prompt_result = prompt_future.result()
            if prompt_result["status"] != "prompt_sent":
                return prompt_result
            if on_prompt_sent:
                on_prompt_sent(prompt_result)

            while not media_future.done():
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GenerateBridgeError("Gemini belum menyelesaikan atau mengunduh hasil dalam batas waktu.")
                await asyncio.wait({media_future}, timeout=min(remaining, 0.25))
            result = media_future.result()
            check_cancelled()
            result.setdefault("chat_id", prompt_result.get("chat_id", ""))
            return result
        finally:
            self._pending.pop(request_id, None)
            for future in (prompt_future, media_future):
                if not future.done():
                    future.cancel()

    def send_prompt(
        self,
        profile_id: str,
        prompt: str,
        check_cancelled: Callable[[], None],
        media_type: str = "video",
        ratio: str = "9:16",
        on_prompt_sent: Optional[Callable[[dict[str, Any]], None]] = None,
        timeout: float = 900,
    ) -> dict[str, Any]:
        if not valid_profile_id(profile_id):
            raise ValueError("ID profil tidak valid.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_LENGTH:
            raise ValueError(f"Prompt wajib diisi dan maksimal {MAX_PROMPT_LENGTH} karakter.")
        if not self._loop or not self._loop.is_running():
            raise GenerateBridgeError("Generate extension bridge belum berjalan.")
        if media_type not in ("video", "image"):
            raise ValueError("Jenis media tidak valid.")
        if ratio not in ("9:16", "16:9"):
            raise ValueError("Rasio media tidak valid.")
        future = asyncio.run_coroutine_threadsafe(
            self._send_prompt(
                profile_id,
                prompt.strip(),
                media_type,
                ratio,
                check_cancelled,
                on_prompt_sent,
                timeout,
            ),
            self._loop,
        )
        try:
            return future.result(timeout=timeout + 2)
        except FutureTimeout as exc:
            future.cancel()
            raise GenerateBridgeError("Generate extension bridge tidak merespons.") from exc


def pair_page_html() -> str:
    return """<!doctype html>
<html lang="id"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AMB · Menyiapkan profil</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#10171b;color:#e8f0f2;font:16px/1.6 system-ui,sans-serif}
main{width:min(480px,calc(100% - 40px));padding:28px;border:1px solid #293941;border-radius:16px;background:#151f24}
h1{font-size:1.3rem;margin:0 0 8px}p{color:#aab8bf}button{padding:10px 16px;border:0;border-radius:8px;background:#42c7b5;color:#09201e;font-weight:700;cursor:pointer}
#continue{display:none}
</style>
<main><h1>Menyiapkan profil AMB</h1><p id="status">Extension AMB sedang memasangkan profil ini. Halaman akan lanjut ke Gemini otomatis.</p>
<button id="continue" type="button">Lanjut ke Gemini</button></main>
<script>
const button=document.querySelector("#continue");
button.onclick=()=>location.replace("https://gemini.google.com/app");
setTimeout(()=>{button.style.display="inline-block";document.querySelector("#status").textContent="Extension belum tersambung. Pastikan extension AMB sudah di-load pada profil ini, lalu coba buka ulang profil dari AMB.";},8000);
</script></html>"""
