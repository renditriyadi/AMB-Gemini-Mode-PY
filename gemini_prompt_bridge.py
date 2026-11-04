#!/usr/bin/env python3
"""Local-only WebSocket bridge from the Python CLI to Gemini Chrome extensions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


HOST = "127.0.0.1"
PORT = 8765
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
BRIDGE_KEY_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AMB Media V5" / "gemini-prompt-bridge.key"


def load_or_create_bridge_key() -> str:
    BRIDGE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        key = BRIDGE_KEY_PATH.read_text(encoding="utf-8").strip()
        if key:
            return key
    except FileNotFoundError:
        pass

    key = secrets.token_urlsafe(32)
    try:
        with BRIDGE_KEY_PATH.open("x", encoding="utf-8") as key_file:
            key_file.write(key)
    except FileExistsError:
        return BRIDGE_KEY_PATH.read_text(encoding="utf-8").strip()
    return key


def valid_profile_id(profile_id: Any) -> bool:
    return isinstance(profile_id, str) and bool(PROFILE_ID_PATTERN.fullmatch(profile_id))


async def run_bridge() -> None:
    bridge_key = load_or_create_bridge_key()
    clients: dict[str, ServerConnection] = {}
    pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
    loop = asyncio.get_running_loop()

    async def handle_client(websocket: ServerConnection) -> None:
        profile_id = None
        origin = websocket.request.headers.get("Origin", "")
        if not origin.startswith("chrome-extension://"):
            await websocket.close(code=1008, reason="Only Chrome extensions are accepted")
            return

        try:
            async for raw_message in websocket:
                try:
                    message = json.loads(raw_message)
                except (TypeError, json.JSONDecodeError):
                    continue

                if message.get("type") == "hello":
                    requested_profile = message.get("profileId")
                    supplied_key = message.get("bridgeKey")
                    if not valid_profile_id(requested_profile) or not secrets.compare_digest(str(supplied_key or ""), bridge_key):
                        await websocket.close(code=1008, reason="Invalid profile ID or bridge key")
                        return
                    profile_id = requested_profile
                    old_client = clients.get(profile_id)
                    if old_client and old_client is not websocket:
                        await old_client.close(code=4001, reason="Replaced by another tab")
                    clients[profile_id] = websocket
                    print(f"Extension terhubung untuk profil: {profile_id}")
                    await websocket.send(json.dumps({"type": "hello_ack", "profileId": profile_id}))
                    continue

                if message.get("type") == "command_result":
                    request_id = str(message.get("requestId") or "")
                    future = pending.get(request_id)
                    if future and not future.done():
                        future.set_result(message)
        except Exception as error:
            print(f"Koneksi extension berakhir: {type(error).__name__}")
        finally:
            if profile_id and clients.get(profile_id) is websocket:
                clients.pop(profile_id, None)
                print(f"Extension terputus dari profil: {profile_id}")

    async def send_prompt(profile_id: str, prompt: str) -> None:
        websocket = clients.get(profile_id)
        if not websocket:
            print("Tidak ada extension tersambung untuk profil itu.")
            return

        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        pending[request_id] = future
        try:
            await websocket.send(json.dumps({
                "type": "generate_prompt",
                "requestId": request_id,
                "profileId": profile_id,
                "prompt": prompt,
            }, ensure_ascii=False))
            print("Prompt dikirim ke extension; menunggu status composer Gemini...")
            result = await asyncio.wait_for(future, timeout=30)
            print(f"Status: {result.get('status', 'unknown')}")
            if result.get("detail"):
                print(f"Info: {result['detail']}")
        except asyncio.TimeoutError:
            print("Extension tidak memberi status dalam 30 detik.")
        except Exception as error:
            print(f"Gagal mengirim prompt: {type(error).__name__}")
        finally:
            pending.pop(request_id, None)

    async with serve(handle_client, HOST, PORT, max_size=16_384, ping_interval=20, ping_timeout=20):
        print("Gemini Prompt Bridge aktif hanya di komputer ini (127.0.0.1:8765).")
        print(f"Kunci bridge lokal (bukan password akun): {bridge_key}")
        print("Masukkan ID profil Chrome yang sudah dikonfigurasi di popup extension.")
        print("Ketik 0 sebagai ID profil untuk berhenti.\n")

        while True:
            try:
                profile_id = (await asyncio.to_thread(input, "ID profil target (0=keluar): ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if profile_id == "0":
                break
            if not valid_profile_id(profile_id):
                print("ID profil tidak valid.")
                continue
            if profile_id not in clients:
                print("Profil belum terhubung. Buka extension pada profil itu dan simpan pengaturannya.")
                continue

            prompt = (await asyncio.to_thread(input, "Prompt Gemini: ")).strip()
            if not prompt:
                print("Prompt kosong; tidak dikirim.")
                continue
            if len(prompt) > 5000:
                print("Prompt maksimal 5000 karakter.")
                continue
            await send_prompt(profile_id, prompt)

        for websocket in list(clients.values()):
            await websocket.close(code=1001, reason="Bridge stopped")
        print("Bridge dihentikan.")


if __name__ == "__main__":
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("Bridge dihentikan.")