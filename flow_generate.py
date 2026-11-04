"""UI automation for Google Flow through a managed, loopback-only Chrome CDP session."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


FLOW_URL = "https://flow.google.com/"
FLOW_GENERATION_TIMEOUT = 15 * 60
FLOW_MODELS = (
    "Omni 1.1 Flash",
    "Veo 3.1 - Lite",
    "Veo 3.1 - Fast",
    "Veo 3.1 - Quality",
)
FLOW_IMAGE_MODELS = (
    "Nano Banana Pro",
    "Nano Banana 2 Lite",
    "Nano Banana 2.1",
)
FLOW_IMAGE_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16")
_CREDIT_COST = re.compile(r"Pembuatan akan menggunakan\s+(\d+)\s+kredit", re.IGNORECASE)


class FlowGenerateError(RuntimeError):
    pass


def browser_url(profile_dir: Path) -> Optional[str]:
    active_port = profile_dir / "DevToolsActivePort"
    try:
        lines = active_port.read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
        if not 1 <= port <= 65535:
            return None
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
            version = json.load(response)
    except (OSError, UnicodeError, ValueError, IndexError, json.JSONDecodeError, URLError):
        return None

    if not isinstance(version, dict):
        return None
    websocket_url = version.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str):
        return None
    try:
        parsed = urlsplit(websocket_url)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port != port:
            return None
    except ValueError:
        return None
    return f"http://127.0.0.1:{port}"


def is_ready(profile_dir: Path) -> bool:
    return browser_url(profile_dir) is not None


def _flow_page(browser):
    if not browser.contexts:
        raise FlowGenerateError("Chrome Flow tidak memiliki konteks browser yang aktif.")
    context = browser.contexts[0]
    page = next((candidate for candidate in context.pages if "flow.google.com" in candidate.url), None)
    return page or context.new_page()


def open_flow(profile_dir: Path) -> None:
    endpoint = browser_url(profile_dir)
    if not endpoint:
        raise FlowGenerateError("Chrome profil ini tidak memiliki CDP loopback yang aktif.")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            try:
                page = _flow_page(browser)
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
            finally:
                browser.close()
    except FlowGenerateError:
        raise
    except Exception as exc:
        raise FlowGenerateError(f"Gagal membuka Google Flow: {exc}") from exc


def _select_radio(page, name: str, *, pattern: bool = False) -> None:
    locator = page.get_by_role("radio", name=re.compile(name, re.IGNORECASE) if pattern else name).first
    if locator.get_attribute("aria-checked") != "true":
        locator.click()


def _select_model(page, model: str, *, image: bool = False) -> None:
    supported_models = FLOW_IMAGE_MODELS if image else FLOW_MODELS
    if model not in supported_models:
        raise FlowGenerateError(f"Model Flow tidak didukung: {model}.")
    selector = page.get_by_role("button", name="Pilih rangkaian model")
    if model in selector.inner_text():
        return
    selector.click()
    option = page.get_by_role("menuitem", name=re.compile(re.escape(model), re.IGNORECASE))
    if option.count() != 1:
        raise FlowGenerateError(f"Pilihan model '{model}' tidak tersedia pada akun Flow ini.")
    option.click()


def ensure_agent_inactive(page) -> None:
    agent_button = page.locator("button.agent-mode-chip")
    try:
        agent_button.wait_for(state="visible", timeout=15000)
    except PlaywrightTimeoutError as exc:
        raise FlowGenerateError(
            "Status Agen Flow tidak dapat diverifikasi; prompt tidak dikirim."
        ) from exc
    if agent_button.count() != 1:
        raise FlowGenerateError("Status Agen Flow tidak dapat diverifikasi; prompt tidak dikirim.")

    pressed = agent_button.get_attribute("aria-pressed")
    if pressed == "true":
        agent_button.click()
        page.wait_for_function(
            "document.querySelector('button.agent-mode-chip')?.getAttribute('aria-pressed') === 'false'",
            timeout=5000,
        )
    elif pressed != "false":
        raise FlowGenerateError("Status Agen Flow tidak jelas; prompt tidak dikirim.")

    if agent_button.get_attribute("aria-pressed") != "false":
        raise FlowGenerateError("Agen Flow masih aktif; prompt tidak dikirim.")

    active_notice = page.get_by_text(re.compile(r"Agen Anda aktif", re.IGNORECASE))
    if active_notice.count() and active_notice.first.is_visible():
        raise FlowGenerateError("Peringatan Agen aktif masih tampil; prompt tidak dikirim.")


def _flow_media_id(source: str) -> Optional[str]:
    match = re.search(r"/image/([^/?#]+)", source)
    return match.group(1) if match else None


def _accept_upload_rights_if_present(page, report: Callable[[str], None]) -> bool:
    notice = page.get_by_text(
        re.compile(r"Hak untuk menggunakan (?:video|gambar) ini", re.IGNORECASE),
        exact=True,
    )
    if not notice.count() or not notice.first.is_visible():
        return False

    agree = page.get_by_role("button", name="Saya setuju", exact=True)
    if agree.count() != 1 or not agree.is_visible() or not agree.is_enabled():
        raise FlowGenerateError(
            "Dialog hak penggunaan Flow tampil, tetapi tombol 'Saya setuju' tidak tersedia."
        )
    agree.click()
    report("Konfirmasi hak penggunaan upload Flow disetujui.\n")
    return True


def _wait_for_uploaded_asset(
    page,
    option,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
    timeout: int,
    media_label: str,
):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        check_cancelled()
        _accept_upload_rights_if_present(page, report)
        count = option.count()
        if count > 1:
            raise FlowGenerateError(
                f"{media_label} referensi tidak dapat dibedakan dari media Flow lainnya."
            )
        if count == 1 and option.is_visible():
            page.wait_for_timeout(750)
            check_cancelled()
            if _accept_upload_rights_if_present(page, report):
                continue
            if option.count() == 1 and option.is_visible():
                return option
        page.wait_for_timeout(250)
    raise FlowGenerateError(f"{media_label} referensi tidak muncul di daftar media Flow.")


def _wait_for_add_to_prompt(
    page,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
    media_label: str,
    *,
    ready: Optional[Callable[[], bool]] = None,
    attached: Optional[Callable[[], bool]] = None,
    timeout: int = 5 * 60 * 1000,
):
    add_to_prompt = page.get_by_role("button", name="Tambahkan ke perintah")
    deadline = time.monotonic() + timeout / 1000
    reported_wait = False
    while time.monotonic() < deadline:
        check_cancelled()
        _accept_upload_rights_if_present(page, report)
        if attached is not None and attached():
            return None
        if add_to_prompt.count() > 1:
            raise FlowGenerateError("Tombol untuk menempelkan media ke prompt Flow tidak unik.")
        if (
            add_to_prompt.count() == 1
            and add_to_prompt.is_visible()
            and add_to_prompt.is_enabled()
            and (ready is None or ready())
        ):
            return add_to_prompt
        if not reported_wait:
            report(f"Menunggu Flow menyelesaikan pemrosesan {media_label} referensi...\n")
            reported_wait = True
        page.wait_for_timeout(500)
    raise FlowGenerateError(
        f"Flow belum menyelesaikan pemrosesan {media_label} referensi setelah 5 menit."
    )


def _wait_for_prompt_media_id(
    page,
    selector: str,
    media_id: Optional[str],
    check_cancelled: Callable[[], None],
    media_label: str,
    timeout: int = 30000,
) -> str:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        check_cancelled()
        sources = page.locator(selector).evaluate_all(
            "elements => elements.map(image => image.currentSrc || image.src)"
        )
        matching_id = next(
            (
                source_id
                for source in sources
                if (source_id := _flow_media_id(source))
                and (media_id is None or source_id == media_id)
            ),
            None,
        )
        if matching_id:
            return matching_id
        page.wait_for_timeout(500)
    raise FlowGenerateError(
        f"ID {media_label} referensi tidak terpasang pada kotak perintah Flow."
    )


def _attach_reference_image(
    page,
    image_path: Path,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
) -> str:
    image_path = Path(image_path)
    if (
        not image_path.is_file()
        or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
    ):
        raise FlowGenerateError("Gambar referensi tidak ditemukan atau formatnya tidak didukung.")

    check_cancelled()
    add_material = page.get_by_role("button", name="Tambahkan bahan ke kotak perintah")
    if add_material.get_attribute("aria-expanded") != "true":
        add_material.click()
    upload = page.get_by_role("button", name="Upload media")
    try:
        upload.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError as exc:
        raise FlowGenerateError("Tombol upload gambar Flow tidak tersedia.") from exc
    if upload.count() != 1:
        raise FlowGenerateError("Tombol upload gambar Flow tidak tersedia.")
    with page.expect_file_chooser(timeout=15000) as chooser_info:
        upload.click()
    chooser_info.value.set_files(str(image_path))

    option = page.get_by_role(
        "option",
        name=re.compile(rf"^{re.escape(image_path.name)}(?:\s|$)"),
    )
    _wait_for_uploaded_asset(page, option, check_cancelled, report, 30000, "Gambar")

    media_id = _flow_media_id(option.locator("img").get_attribute("src") or "")
    if not media_id:
        raise FlowGenerateError("Flow tidak memberikan ID untuk gambar referensi yang diunggah.")

    check_cancelled()
    option.click()
    composer_selector = "flow-base-prompt-box img"

    def composer_has_image() -> bool:
        sources = page.locator("flow-base-prompt-box img").evaluate_all(
            "elements => elements.map(image => image.currentSrc || image.src)"
        )
        return any(_flow_media_id(source) == media_id for source in sources)

    def preview_has_image() -> bool:
        preview_sources = page.locator(
            'img[alt="Gambar bahan"], img[alt^="Pratinjau "]'
        ).evaluate_all(
            "elements => elements.map(image => image.currentSrc || image.src)"
        )
        return any(_flow_media_id(source) == media_id for source in preview_sources)

    add_to_prompt = _wait_for_add_to_prompt(
        page,
        check_cancelled,
        report,
        "gambar",
        ready=preview_has_image,
        attached=composer_has_image,
    )
    if add_to_prompt is not None:
        check_cancelled()
        add_to_prompt.click()
        _wait_for_prompt_media_id(
            page,
            composer_selector,
            media_id,
            check_cancelled,
            "gambar",
        )

    report(f"Gambar referensi terpasang di Flow (ID media {media_id}).\n")
    return media_id


def _attach_reference_video(
    page,
    video_path: Path,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
) -> str:
    video_path = Path(video_path)
    if not video_path.is_file() or video_path.suffix.lower() not in {
        ".mp4", ".webm", ".mov", ".m4v",
    }:
        raise FlowGenerateError("Video referensi tidak ditemukan atau formatnya tidak didukung.")

    check_cancelled()
    add_material = page.get_by_role("button", name="Tambahkan bahan ke kotak perintah")
    if add_material.get_attribute("aria-expanded") != "true":
        add_material.click()
    upload = page.get_by_role("button", name="Upload media")
    try:
        upload.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError as exc:
        raise FlowGenerateError("Tombol upload video Flow tidak tersedia.") from exc
    if upload.count() != 1:
        raise FlowGenerateError("Tombol upload video Flow tidak tersedia.")
    with page.expect_file_chooser(timeout=15000) as chooser_info:
        upload.click()
    chooser_info.value.set_files(str(video_path))

    option = page.get_by_role("option").filter(has_text=video_path.stem)
    _wait_for_uploaded_asset(page, option, check_cancelled, report, 90000, "Video")

    check_cancelled()
    option.click()
    video_chip_selector = (
        'flow-base-prompt-box flow-video-ingredient-chip img[alt="Gambar bahan video"]'
    )
    video_chip = page.locator(video_chip_selector)

    def attached_video_id() -> Optional[str]:
        sources = video_chip.evaluate_all(
            "elements => elements.map(image => image.currentSrc || image.src)"
        )
        return next((media_id for source in sources if (media_id := _flow_media_id(source))), None)

    add_to_prompt = _wait_for_add_to_prompt(
        page,
        check_cancelled,
        report,
        "video",
        attached=lambda: bool(attached_video_id()),
    )
    if add_to_prompt is not None:
        check_cancelled()
        add_to_prompt.click()
        media_id = _wait_for_prompt_media_id(
            page,
            video_chip_selector,
            None,
            check_cancelled,
            "video",
        )
    else:
        media_id = attached_video_id()
    if not media_id:
        raise FlowGenerateError("Flow tidak memberikan ID untuk video referensi yang diunggah.")

    report(f"Video referensi terpasang di Flow (ID media {media_id}).\n")
    return media_id


def _configure_generation(
    page,
    prompt: str,
    ratio: str,
    model: str,
    media_type: str,
    check_cancelled: Callable[[], None],
    *,
    resolution: str = "",
    duration: str = "",
) -> Optional[re.Match[str]]:
    image = media_type == "image"
    models = FLOW_IMAGE_MODELS if image else FLOW_MODELS
    ratios = FLOW_IMAGE_RATIOS if image else ("9:16", "16:9")
    if media_type not in ("image", "video"):
        raise FlowGenerateError(f"Jenis media Flow tidak didukung: {media_type}.")
    if model not in models:
        raise FlowGenerateError(f"Model Flow tidak didukung: {model}.")
    if ratio not in ratios:
        raise FlowGenerateError(f"Rasio Flow tidak didukung: {ratio}.")
    if not image:
        if resolution not in ("360p", "720p"):
            raise FlowGenerateError(f"Resolusi Flow tidak didukung: {resolution}.")
        if duration not in ("4 dtk", "6 dtk", "8 dtk", "10 dtk"):
            raise FlowGenerateError(f"Durasi Flow tidak didukung: {duration}.")

    page.get_by_role("button", name=re.compile("Project baru", re.IGNORECASE)).click()
    page.wait_for_url(re.compile(r"https://flow\.google\.com/project/"), timeout=30000)
    page.get_by_role("textbox", name="Apa yang ingin Anda buat?").wait_for(
        state="visible", timeout=30000
    )
    ensure_agent_inactive(page)
    page.get_by_role("textbox", name="Apa yang ingin Anda buat?").fill(prompt)
    check_cancelled()

    page.get_by_role("button", name="Pemicu setelan").click()
    _select_radio(page, "Gambar" if image else "Video")
    _select_radio(page, ratio)
    _select_model(page, model, image=image)
    if not image:
        _select_radio(page, resolution)
        _select_radio(page, duration)
    _select_radio(page, "x1")

    cost_match = _CREDIT_COST.search(page.locator("body").inner_text())
    page.keyboard.press("Escape")
    check_cancelled()
    return cost_match


def generate_video(
    profile_dir: Path,
    prompt: str,
    ratio: str,
    model: str,
    resolution: str,
    duration: str,
    download_path: Path,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
    *,
    reference_image_path: Optional[Path] = None,
    reference_video_path: Optional[Path] = None,
) -> None:
    endpoint = browser_url(profile_dir)
    if not endpoint:
        raise FlowGenerateError("CDP Flow tidak aktif. Buka profil dengan tombol Flow di AMB.")

    download_path.parent.mkdir(parents=True, exist_ok=True)
    download_path.unlink(missing_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            try:
                page = _flow_page(browser)
                page.set_default_timeout(10000)
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
                if "accounts.google.com" in page.url:
                    raise FlowGenerateError("Profil belum login ke Google Flow.")

                cost_match = _configure_generation(
                    page,
                    prompt,
                    ratio,
                    model,
                    "video",
                    check_cancelled,
                    resolution=resolution,
                    duration=duration,
                )

                if reference_image_path:
                    _attach_reference_image(
                        page,
                        reference_image_path,
                        check_cancelled,
                        report,
                    )
                elif reference_video_path:
                    _attach_reference_video(
                        page,
                        reference_video_path,
                        check_cancelled,
                        report,
                    )

                baseline = page.locator('img[alt="Thumbnail video yang dihasilkan"]').count()
                page.get_by_role("button", name="Mulai pembuatan").click()
                cost_detail = f"; estimasi: {cost_match.group(1)} kredit" if cost_match else ""
                report(
                    f"Flow menerima prompt. Model: {model}; {resolution}, "
                    f"{duration.removesuffix(' dtk')} detik, 1 video{cost_detail}.\n"
                )

                deadline = time.monotonic() + FLOW_GENERATION_TIMEOUT
                while time.monotonic() < deadline:
                    check_cancelled()
                    if page.locator('img[alt="Thumbnail video yang dihasilkan"]').count() > baseline:
                        break
                    time.sleep(2)
                else:
                    raise FlowGenerateError("Flow belum menampilkan video setelah batas waktu 15 menit.")

                check_cancelled()
                page.locator('img[alt="Thumbnail video yang dihasilkan"]').last.click()
                page.get_by_role("button", name="Download media").click()
                menu_items = page.get_by_role("menuitem")
                original_resolution = menu_items.filter(
                    has_text=re.compile(rf"\b{re.escape(resolution)}\b")
                )
                if original_resolution.count() != 1:
                    raise FlowGenerateError(
                        f"Pilihan unduh {resolution} tidak ditemukan; video belum diunduh."
                    )
                with page.expect_download(timeout=120000) as download_info:
                    original_resolution.click()
                download_info.value.save_as(str(download_path))
                if not download_path.is_file() or download_path.stat().st_size == 0:
                    raise FlowGenerateError("Unduhan video Flow kosong atau tidak ditemukan.")
            finally:
                browser.close()
    except FlowGenerateError:
        download_path.unlink(missing_ok=True)
        raise
    except PlaywrightTimeoutError as exc:
        download_path.unlink(missing_ok=True)
        raise FlowGenerateError(f"Flow tidak merespons kontrol UI yang diperlukan: {exc}") from exc
    except Exception as exc:
        download_path.unlink(missing_ok=True)
        raise FlowGenerateError(f"Generate Flow gagal: {exc}") from exc


def generate_image(
    profile_dir: Path,
    prompt: str,
    ratio: str,
    model: str,
    download_path: Path,
    check_cancelled: Callable[[], None],
    report: Callable[[str], None],
    reference_image_path: Optional[Path] = None,
    *,
    reference_video_path: Optional[Path] = None,
) -> Path:
    endpoint = browser_url(profile_dir)
    if not endpoint:
        raise FlowGenerateError("CDP Flow tidak aktif. Buka profil dengan tombol Flow di AMB.")

    download_path.parent.mkdir(parents=True, exist_ok=True)
    download_path.unlink(missing_ok=True)
    final_path = download_path
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            try:
                page = _flow_page(browser)
                page.set_default_timeout(10000)
                page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30000)
                if "accounts.google.com" in page.url:
                    raise FlowGenerateError("Profil belum login ke Google Flow.")

                cost_match = _configure_generation(
                    page, prompt, ratio, model, "image", check_cancelled
                )
                if reference_image_path:
                    _attach_reference_image(
                        page,
                        reference_image_path,
                        check_cancelled,
                        report,
                    )
                elif reference_video_path:
                    _attach_reference_video(
                        page,
                        reference_video_path,
                        check_cancelled,
                        report,
                    )
                image_tiles = page.locator("img[data-media-id]")
                baseline_ids = set(
                    image_tiles.evaluate_all(
                        "elements => elements.map(element => element.getAttribute('data-media-id'))"
                    )
                )
                page.get_by_role("button", name="Mulai pembuatan").click()
                cost_detail = f"; estimasi: {cost_match.group(1)} kredit" if cost_match else ""
                report(f"Flow menerima prompt gambar. Model: {model}; rasio {ratio}, 1 gambar{cost_detail}.\n")

                deadline = time.monotonic() + FLOW_GENERATION_TIMEOUT
                generated_image_id = None
                while time.monotonic() < deadline:
                    check_cancelled()
                    current_ids = image_tiles.evaluate_all(
                        "elements => elements.map(element => element.getAttribute('data-media-id'))"
                    )
                    generated_ids = [media_id for media_id in current_ids if media_id not in baseline_ids]
                    if generated_ids:
                        generated_image_id = generated_ids[-1]
                        break
                    time.sleep(2)
                else:
                    raise FlowGenerateError("Flow belum menampilkan gambar setelah batas waktu 15 menit.")

                check_cancelled()
                page.locator(
                    f'img[data-media-id="{generated_image_id}"]'
                ).click()
                page.get_by_role("button", name="Download media").click()
                menu_items = page.get_by_role("menuitem")
                original_download = menu_items.filter(
                    has_text=re.compile(r"ukuran asli|original", re.IGNORECASE)
                )
                high_resolution_download = menu_items.filter(
                    has_text=re.compile(r"2k", re.IGNORECASE)
                )
                standard_download = menu_items.filter(
                    has_text=re.compile(r"1k", re.IGNORECASE)
                )
                if original_download.count() == 1:
                    download_option = original_download
                elif high_resolution_download.count() == 1:
                    download_option = high_resolution_download
                elif standard_download.count() == 1:
                    download_option = standard_download
                elif menu_items.count() == 1:
                    download_option = menu_items
                else:
                    raise FlowGenerateError(
                        "Pilihan unduh gambar asli Flow tidak dapat ditentukan."
                    )

                with page.expect_download(timeout=120000) as download_info:
                    download_option.click()
                download = download_info.value
                extension = Path(download.suggested_filename).suffix.lower()
                if extension not in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
                    raise FlowGenerateError(
                        f"Format gambar Flow tidak didukung: {extension or 'tanpa ekstensi'}."
                    )
                final_path = Path(f"{download_path}{extension}")
                final_path.unlink(missing_ok=True)
                download.save_as(str(final_path))
                if not final_path.is_file() or final_path.stat().st_size == 0:
                    raise FlowGenerateError("Unduhan gambar Flow kosong atau tidak ditemukan.")
            finally:
                browser.close()
        return final_path
    except FlowGenerateError:
        download_path.unlink(missing_ok=True)
        if final_path != download_path:
            final_path.unlink(missing_ok=True)
        raise
    except PlaywrightTimeoutError as exc:
        download_path.unlink(missing_ok=True)
        if final_path != download_path:
            final_path.unlink(missing_ok=True)
        raise FlowGenerateError(f"Flow tidak merespons kontrol UI yang diperlukan: {exc}") from exc
    except Exception as exc:
        download_path.unlink(missing_ok=True)
        if final_path != download_path:
            final_path.unlink(missing_ok=True)
        raise FlowGenerateError(f"Generate gambar Flow gagal: {exc}") from exc
