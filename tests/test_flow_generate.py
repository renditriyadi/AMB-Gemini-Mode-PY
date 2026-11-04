import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import flow_generate


class FakeAgentButton:
    def __init__(self, state):
        self.state = state

    def count(self):
        return 1

    def get_attribute(self, name):
        return self.state if name == "aria-pressed" else None

    def click(self):
        self.state = "false"

    def wait_for(self, state, timeout):
        if state != "visible":
            raise AssertionError(f"Unexpected wait state: {state}")


class FakeAgentNotice:
    def __init__(self, button):
        self.button = button
        self.first = self

    def count(self):
        return int(self.button.state == "true")

    def is_visible(self):
        return self.button.state == "true"


class FakeAgentPage:
    def __init__(self, state):
        self.button = FakeAgentButton(state)

    def locator(self, selector):
        return self.button

    def wait_for_function(self, expression, timeout):
        if self.button.state != "false":
            raise AssertionError("Agent did not turn off")

    def get_by_text(self, pattern):
        return FakeAgentNotice(self.button)


class FakeConsentButton:
    def __init__(self, visible=True):
        self.visible = visible
        self.clicked = False

    def count(self):
        return int(self.visible)

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.visible

    def click(self):
        self.clicked = True
        self.visible = False


class FakeConsentNotice:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    def count(self):
        return int(self.visible)

    def is_visible(self):
        return self.visible


class FakeConsentPage:
    def __init__(self, notice_visible=True, button_visible=True, media="video"):
        self.notice = FakeConsentNotice(notice_visible)
        self.media = media
        self.button = FakeConsentButton(button_visible)

    def get_by_text(self, pattern, exact=False):
        if not exact or not pattern.fullmatch(f"Hak untuk menggunakan {self.media} ini"):
            raise AssertionError("Expected an exact Flow upload rights notice")
        return self.notice

    def get_by_role(self, role, name, exact=False):
        if role != "button" or name != "Saya setuju" or not exact:
            raise AssertionError("Expected the exact Flow consent button")
        return self.button


class FakeUploadPollButton:
    def __init__(self):
        self.enabled = False

    def count(self):
        return 1

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.enabled


class FakeHiddenNotice:
    def count(self):
        return 0

    def is_visible(self):
        return False


class FakeUploadPollPage:
    def __init__(self):
        self.button = FakeUploadPollButton()
        self.waits = 0

    def get_by_text(self, pattern, exact=False):
        return FakeHiddenNotice()

    def get_by_role(self, role, name):
        if role != "button" or name != "Tambahkan ke perintah":
            raise AssertionError("Expected the Flow add-to-prompt button")
        return self.button

    def wait_for_timeout(self, timeout):
        self.waits += 1
        if self.waits >= 2:
            self.button.enabled = True


class FakeAttachedUploadPollPage(FakeUploadPollPage):
    def __init__(self):
        super().__init__()
        self.ready = False

    def wait_for_timeout(self, timeout):
        self.waits += 1
        self.ready = True


class FlowCdpTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.temporary_directory.name)
        (self.profile_dir / "DevToolsActivePort").write_text(
            "9222\n/devtools/browser/test\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_browser_url_accepts_loopback_cdp_endpoint(self):
        payload = json.dumps({
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test",
        }).encode()
        with patch("flow_generate.urlopen", return_value=BytesIO(payload)):
            self.assertEqual(
                flow_generate.browser_url(self.profile_dir),
                "http://127.0.0.1:9222",
            )

    def test_browser_url_rejects_non_loopback_endpoint(self):
        payload = json.dumps({
            "webSocketDebuggerUrl": "ws://192.168.1.10:9222/devtools/browser/test",
        }).encode()
        with patch("flow_generate.urlopen", return_value=BytesIO(payload)):
            self.assertIsNone(flow_generate.browser_url(self.profile_dir))

    def test_browser_url_rejects_stale_or_invalid_port_file(self):
        (self.profile_dir / "DevToolsActivePort").write_text("not-a-port\n", encoding="utf-8")
        self.assertIsNone(flow_generate.browser_url(self.profile_dir))

    def test_flow_media_id_extracts_id_from_signed_image_url(self):
        self.assertEqual(
            flow_generate._flow_media_id(
                "https://flow-content.google/image/media-123?Expires=1&Signature=test"
            ),
            "media-123",
        )
        self.assertIsNone(flow_generate._flow_media_id("blob:https://flow.google.com/test"))

    def test_ensure_agent_inactive_turns_off_active_agent(self):
        page = FakeAgentPage("true")

        flow_generate.ensure_agent_inactive(page)

        self.assertEqual(page.button.state, "false")

    def test_ensure_agent_inactive_fails_closed_for_unknown_state(self):
        with self.assertRaises(flow_generate.FlowGenerateError):
            flow_generate.ensure_agent_inactive(FakeAgentPage(None))

    def test_upload_rights_prompt_is_accepted_when_visible(self):
        for media in ("video", "gambar"):
            with self.subTest(media=media):
                page = FakeConsentPage(media=media)
                reports = []

                accepted = flow_generate._accept_upload_rights_if_present(page, reports.append)

                self.assertTrue(accepted)
                self.assertTrue(page.button.clicked)
                self.assertIn("Konfirmasi hak penggunaan upload Flow disetujui.", reports[0])

    def test_upload_rights_prompt_is_ignored_when_not_visible(self):
        page = FakeConsentPage(notice_visible=False)

        accepted = flow_generate._accept_upload_rights_if_present(page, lambda _: None)

        self.assertFalse(accepted)
        self.assertFalse(page.button.clicked)

    def test_upload_rights_prompt_fails_if_agree_button_is_unavailable(self):
        page = FakeConsentPage(button_visible=False)

        with self.assertRaisesRegex(flow_generate.FlowGenerateError, "tombol 'Saya setuju'"):
            flow_generate._accept_upload_rights_if_present(page, lambda _: None)

    def test_upload_waits_until_flow_enables_add_to_prompt(self):
        page = FakeUploadPollPage()
        readiness_checks = []

        button = flow_generate._wait_for_add_to_prompt(
            page,
            lambda: None,
            lambda _: None,
            "video",
            ready=lambda: readiness_checks.append(True) or len(readiness_checks) >= 3,
        )

        self.assertIs(button, page.button)
        self.assertGreaterEqual(page.waits, 2)
        self.assertGreaterEqual(len(readiness_checks), 3)

    def test_upload_poll_accepts_media_attached_while_button_is_disabled(self):
        page = FakeAttachedUploadPollPage()

        button = flow_generate._wait_for_add_to_prompt(
            page,
            lambda: None,
            lambda _: None,
            "video",
            attached=lambda: page.ready,
        )

        self.assertIsNone(button)
        self.assertEqual(page.waits, 1)


if __name__ == "__main__":
    unittest.main()
