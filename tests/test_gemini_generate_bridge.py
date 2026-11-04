import asyncio
import json
import threading
import unittest

import websockets

import gemini_generate_bridge as bridge_module


class GenerateBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = bridge_module.GenerateBridge(port=0)
        self.bridge.start()

    async def asyncTearDown(self):
        self.bridge.stop()

    def test_only_chrome_extension_origins_are_accepted(self):
        self.assertTrue(bridge_module.is_extension_origin("chrome-extension://" + "a" * 32))
        self.assertFalse(bridge_module.is_extension_origin("http://127.0.0.1:8000"))
        self.assertFalse(bridge_module.is_extension_origin("chrome-extension://not-a-valid-id"))

    async def test_one_time_pairing_routes_only_to_bound_profile(self):
        ticket = self.bridge.issue_pairing("profile-one")
        uri = f"ws://127.0.0.1:{self.bridge.port}"
        origin = "chrome-extension://" + "a" * 32

        async with websockets.connect(uri, origin=origin) as extension:
            await extension.send(json.dumps({"type": "pair", "ticket": ticket}))
            paired = json.loads(await extension.recv())
            self.assertEqual(paired["type"], "paired")
            self.assertEqual(paired["profileId"], "profile-one")
            self.assertTrue(self.bridge.status("profile-one")["extension_ready"])
            self.assertFalse(self.bridge.status("profile-two")["extension_ready"])

            prompt_results = []
            prompt_acknowledged = threading.Event()
            def on_prompt_sent(result):
                prompt_results.append(result)
                prompt_acknowledged.set()

            send_future = asyncio.create_task(asyncio.to_thread(
                self.bridge.send_prompt,
                "profile-one",
                "Create a short video",
                lambda: None,
                media_type="video",
                ratio="9:16",
                on_prompt_sent=on_prompt_sent,
                timeout=3,
            ))
            command = json.loads(await asyncio.wait_for(extension.recv(), timeout=2))
            self.assertEqual(command["type"], "generate_prompt")
            self.assertEqual(command["profileId"], "profile-one")
            self.assertEqual(command["prompt"], "Create a short video")
            self.assertEqual(command["mediaType"], "video")
            self.assertEqual(command["ratio"], "9:16")
            await extension.send(json.dumps({
                "type": "command_result",
                "requestId": command["requestId"],
                "profileId": "profile-one",
                "status": "prompt_sent",
                "chatId": "chat-one",
                "detail": "Prompt dikirim.",
            }))
            self.assertTrue(await asyncio.to_thread(prompt_acknowledged.wait, 1))
            self.assertEqual(prompt_results[0]["chat_id"], "chat-one")
            await extension.send(json.dumps({
                "type": "media_result",
                "requestId": command["requestId"],
                "profileId": "profile-one",
                "status": "complete",
                "chatId": "chat-one",
                "downloadPath": "C:\\Users\\test\\Downloads\\gemini.mp4",
                "mimeType": "video/mp4",
                "detail": "Selesai.",
            }))
            result = await send_future
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["chat_id"], "chat-one")
            self.assertEqual(result["download_path"], "C:\\Users\\test\\Downloads\\gemini.mp4")
            self.assertEqual(prompt_results[0]["status"], "prompt_sent")

            async with websockets.connect(uri, origin=origin) as replay:
                await replay.send(json.dumps({"type": "pair", "ticket": ticket}))
                with self.assertRaises(websockets.exceptions.ConnectionClosed):
                    await replay.recv()

    async def test_rejects_non_extension_origin(self):
        uri = f"ws://127.0.0.1:{self.bridge.port}"
        async with websockets.connect(uri, origin="http://127.0.0.1:8000") as client:
            with self.assertRaises(websockets.exceptions.ConnectionClosed):
                await client.recv()

    async def test_missing_extension_status_is_reported_as_error(self):
        ticket = self.bridge.issue_pairing("profile-one")
        uri = f"ws://127.0.0.1:{self.bridge.port}"
        origin = "chrome-extension://" + "a" * 32

        async with websockets.connect(uri, origin=origin) as extension:
            await extension.send(json.dumps({"type": "pair", "ticket": ticket}))
            await extension.recv()
            send_future = asyncio.create_task(asyncio.to_thread(
                self.bridge.send_prompt,
                "profile-one",
                "Create a short video",
                lambda: None,
                media_type="video",
                ratio="9:16",
                timeout=3,
            ))
            command = json.loads(await asyncio.wait_for(extension.recv(), timeout=2))
            await extension.send(json.dumps({
                "type": "command_result",
                "requestId": command["requestId"],
                "profileId": "profile-one",
            }))

            result = await send_future

        self.assertEqual(result["status"], "error")
        self.assertIn("tidak mengembalikan status", result["detail"])


if __name__ == "__main__":
    unittest.main()
