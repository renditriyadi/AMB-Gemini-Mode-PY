import unittest
from unittest.mock import patch

import create_chrome_profile as ccp


class FakeToggle:
    def __init__(self, checked):
        self.checked = checked
        self.click_count = 0

    def wait_for(self, **_kwargs):
        return None

    def get_attribute(self, name):
        return "true" if name == "aria-checked" and self.checked else "false"

    def click(self, **_kwargs):
        self.click_count += 1
        self.checked = True


class FakePage:
    def __init__(self, toggle):
        self.toggle = toggle
        self.url = "https://gemini.google.com/app"
        self.visited = []

    def goto(self, url, **_kwargs):
        self.url = url
        self.visited.append(url)

    def locator(self, _selector):
        return self.toggle

    def wait_for_timeout(self, _timeout):
        return None

    def is_closed(self):
        return False


class ChromeDeveloperModeTests(unittest.TestCase):
    def test_enables_toggle_and_returns_to_gemini(self):
        toggle = FakeToggle(checked=False)
        page = FakePage(toggle)

        self.assertTrue(ccp._enable_extensions_developer_mode(page))

        self.assertEqual(toggle.click_count, 1)
        self.assertEqual(page.visited, ["chrome://extensions/", ccp.DEFAULT_URL])

    def test_leaves_already_enabled_toggle_unchanged(self):
        toggle = FakeToggle(checked=True)
        page = FakePage(toggle)

        self.assertTrue(ccp._enable_extensions_developer_mode(page))

        self.assertEqual(toggle.click_count, 0)
        self.assertEqual(page.url, ccp.DEFAULT_URL)

    def test_reports_failure_when_toggle_cannot_be_enabled(self):
        class UnresponsiveToggle(FakeToggle):
            def click(self, **_kwargs):
                self.click_count += 1

        toggle = UnresponsiveToggle(checked=False)
        page = FakePage(toggle)

        with patch("create_chrome_profile.time.monotonic", side_effect=[0, 6]):
            self.assertFalse(ccp._enable_extensions_developer_mode(page))
        self.assertEqual(page.url, ccp.DEFAULT_URL)


if __name__ == "__main__":
    unittest.main()
