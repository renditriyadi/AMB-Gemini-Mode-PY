#!/usr/bin/env python3
"""Open local Chrome with a persistent, isolated user-data profile."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


DEFAULT_URL = "https://gemini.google.com/app"
GOOGLE_SIGN_IN_URL = "https://accounts.google.com/signin/v2/identifier?flowName=GlifWebSignIn&flowEntry=ServiceLogin"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
WINDOWS_RESERVED_PROFILE_PATTERN = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
DEFAULT_PROFILES_DIR = Path(
    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
) / "AMB Media V5" / "ChromeProfiles"


def find_chrome() -> Optional[Path]:
    candidates = []

    path_candidate = shutil.which("chrome")
    if path_candidate:
        candidates.append(Path(path_candidate))

    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    if sys.platform == "win32":
        try:
            import winreg

            registry_locations = (
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            )
            for hive, key_path in registry_locations:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        executable, _ = winreg.QueryValueEx(key, None)
                    candidates.append(Path(executable))
                except OSError:
                    continue
        except ImportError:
            pass

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Buat dan buka profil Chrome terpisah untuk pengujian. Jalankan tanpa argumen untuk menu."
    )
    parser.add_argument(
        "--profile-id",
        default=None,
        help="Langsung buka ID profil ini tanpa menu, misalnya gemini-test-01.",
    )
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=DEFAULT_PROFILES_DIR,
        help="Folder induk untuk profil Chrome terpisah.",
    )
    parser.add_argument(
        "--chrome",
        type=Path,
        default=None,
        help="Lokasi chrome.exe jika Chrome tidak ditemukan otomatis.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Alamat yang dibuka saat memakai --profile-id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tampilkan perintah tanpa membuat profil atau membuka Chrome.",
    )
    return parser.parse_args()


def get_profile_dir(profiles_dir: Path, profile_id: str) -> Path:
    if not is_valid_profile_id(profile_id):
        raise ValueError(
            "Nama profile harus diawali huruf/angka, maksimal 64 karakter, dan hanya boleh "
            "berisi huruf, angka, spasi, titik, garis bawah, atau tanda hubung. "
            "Nama tidak boleh diakhiri titik/spasi atau memakai nama khusus Windows."
        )
    root = profiles_dir.expanduser().resolve()
    profile_dir = (root / profile_id).resolve()
    if profile_dir.parent != root:
        raise ValueError("Lokasi profil tidak valid.")
    return profile_dir


def is_valid_profile_id(profile_id: str) -> bool:
    return bool(
        PROFILE_ID_PATTERN.fullmatch(profile_id)
        and not profile_id.endswith((" ", "."))
        and not WINDOWS_RESERVED_PROFILE_PATTERN.fullmatch(profile_id)
    )


def list_profiles(profiles_dir: Path) -> list[Path]:
    root = profiles_dir.expanduser().resolve()
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and is_valid_profile_id(path.name)),
        key=lambda path: path.name.lower(),
    )


def create_profile(profiles_dir: Path, profile_id: str) -> None:
    profile_dir = get_profile_dir(profiles_dir, profile_id)
    if profile_dir.exists():
        print(f"Profil '{profile_id}' sudah ada.")
        return
    profile_dir.mkdir(parents=True)
    print(f"Folder profil '{profile_id}' berhasil dibuat.")
    print("Chrome akan mengisi data profil saat profil ini pertama kali dibuka.")


def open_profile(
    chrome: Optional[Path],
    profiles_dir: Path,
    profile_id: str,
    url: Optional[str],
    dry_run: bool = False,
) -> int:
    try:
        profile_dir = get_profile_dir(profiles_dir, profile_id)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if not profile_dir.is_dir():
        if dry_run:
            print(f"Folder profil '{profile_id}' akan dibuat saat Chrome dibuka.")
        else:
            try:
                profile_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"Gagal membuat folder profil: {exc}", file=sys.stderr)
                return 1

    chrome_executable = chrome.expanduser().resolve() if chrome else find_chrome()
    if not chrome_executable or not chrome_executable.is_file():
        print(
            "Google Chrome tidak ditemukan. Gunakan --chrome untuk menunjuk langsung ke chrome.exe.",
            file=sys.stderr,
        )
        return 2

    command = [
        str(chrome_executable),
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if url:
        command.append(url)

    print(f"Membuka profil: {profile_id}")
    print(f"Folder data: {profile_dir}")
    print("Jangan buka ID profil yang sama di dua proses Chrome sekaligus.")

    if dry_run:
        print("Mode uji: Chrome tidak dibuka.")
        print("Perintah:", subprocess.list2cmdline(command))
        return 0

    try:
        subprocess.Popen(command, cwd=str(chrome_executable.parent))
    except OSError as exc:
        print(f"Gagal membuka Chrome: {exc}", file=sys.stderr)
        return 1

    print(f"PROFIL '{profile_id}' BERHASIL DIBUKA.")
    print("Masuk ke Gemini secara manual jika ini pertama kali.")
    return 0


def get_command_user_data_dir(command_line: str) -> Optional[Path]:
    try:
        arguments = shlex.split(command_line, posix=False)
    except ValueError:
        return None

    for index, argument in enumerate(arguments):
        cleaned_argument = argument.strip('"\'')
        if cleaned_argument.casefold() == "--user-data-dir" and index + 1 < len(arguments):
            path_value = arguments[index + 1].strip('"\'')
            return Path(path_value).resolve() if path_value else None
        if cleaned_argument.casefold().startswith("--user-data-dir="):
            path_value = cleaned_argument.split("=", 1)[1]
            return Path(path_value).resolve() if path_value else None
    return None


def find_running_profile_processes(profiles_dir: Path) -> dict[str, list[int]]:
    if sys.platform != "win32":
        raise OSError("Fitur tutup profil otomatis saat ini hanya tersedia di Windows.")

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$ErrorActionPreference = 'Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "Tidak bisa membaca proses Chrome.")

    output = result.stdout.strip()
    if not output:
        return []

    processes = json.loads(output)
    if isinstance(processes, dict):
        processes = [processes]

    managed_profiles = {
        os.path.normcase(str(profile.resolve())).casefold(): profile.name
        for profile in list_profiles(profiles_dir)
    }
    running_profiles: dict[str, list[int]] = {}
    for process in processes:
        command_line = str(process.get("CommandLine") or "")
        if re.search(r"--type(?:=|\s)", command_line, re.IGNORECASE):
            continue
        user_data_dir = get_command_user_data_dir(command_line)
        if not user_data_dir:
            continue
        profile_id = managed_profiles.get(os.path.normcase(str(user_data_dir)).casefold())
        if profile_id:
            running_profiles.setdefault(profile_id, []).append(int(process["ProcessId"]))
    return running_profiles


def close_profile(profiles_dir: Path, profile_id: str, dry_run: bool = False) -> int:
    try:
        get_profile_dir(profiles_dir, profile_id)
        process_ids = find_running_profile_processes(profiles_dir).get(profile_id, [])
    except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Gagal memeriksa Chrome untuk profil '{profile_id}': {exc}", file=sys.stderr)
        return 1

    if not process_ids:
        print(f"Tidak ada Chrome yang sedang berjalan untuk profil '{profile_id}'.")
        return 0

    if dry_run:
        print(f"Mode uji: proses Chrome profil '{profile_id}' yang akan ditutup: {', '.join(map(str, process_ids))}")
        return 0

    failed_ids = []
    for process_id in process_ids:
        result = subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            failed_ids.append(process_id)

    if failed_ids:
        print(f"Gagal menutup proses Chrome: {', '.join(map(str, failed_ids))}.", file=sys.stderr)
        return 1

    print(f"Chrome untuk profil '{profile_id}' berhasil ditutup.")
    return 0


def delete_profiles(
    profiles_dir: Path,
    selected_profiles: list[Path],
    dry_run: bool = False,
    delete_all: bool = False,
) -> int:
    try:
        running_profiles = find_running_profile_processes(profiles_dir)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Gagal memeriksa profil Chrome yang sedang aktif: {exc}", file=sys.stderr)
        return 1

    delete_targets = []
    active_names = []
    for profile in selected_profiles:
        try:
            profile_dir = get_profile_dir(profiles_dir, profile.name)
        except ValueError as exc:
            print(f"Profil dilewati karena lokasinya tidak valid: {profile.name} ({exc})")
            continue

        if profile.name in running_profiles:
            active_names.append(profile.name)
        elif profile_dir.is_dir():
            delete_targets.append(profile_dir)

    if active_names:
        print(f"Profil aktif akan dilewati: {', '.join(active_names)}")
        print("Tutup profil aktif lewat menu 4 terlebih dahulu jika ingin menghapusnya.")
    if not delete_targets:
        print("Tidak ada profil yang bisa dihapus.")
        return 0

    print("\nProfil yang akan dihapus:")
    for profile_dir in delete_targets:
        print(f"  - {profile_dir.name}")

    if dry_run:
        print("Mode uji: tidak ada profil yang dihapus.")
        return 0

    if delete_all:
        prompt = f"Yakin hapus SEMUA {len(delete_targets)} profile tidak aktif? (y/n): "
    else:
        prompt = f"Yakin hapus profile '{delete_targets[0].name}'? (y/n): "
    confirmation = input(prompt).strip().casefold()
    if confirmation not in {"y", "yes"}:
        print("Penghapusan dibatalkan.")
        return 0

    failed_profiles = []
    for profile_dir in delete_targets:
        try:
            shutil.rmtree(profile_dir)
            print(f"Profil '{profile_dir.name}' berhasil dihapus.")
        except OSError as exc:
            failed_profiles.append((profile_dir.name, str(exc)))

    if failed_profiles:
        for profile_name, error in failed_profiles:
            print(f"Gagal menghapus '{profile_name}': {error}", file=sys.stderr)
        return 1
    return 0


def show_profiles(profiles_dir: Path, running_only: bool = False) -> list[Path]:
    profiles = list_profiles(profiles_dir)
    if not profiles:
        print("Belum ada profil. Pilih menu 'Buat Profile' terlebih dahulu.")
        return []

    try:
        running_profiles = find_running_profile_processes(profiles_dir)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Status Chrome tidak bisa dibaca: {exc}", file=sys.stderr)
        if running_only:
            return []
        running_profiles = {}

    if running_only:
        profiles = [profile for profile in profiles if profile.name in running_profiles]
        if not profiles:
            print("Tidak ada Chrome aktif yang memakai profil dari daftar skrip ini.")
            return []

    print("\nDaftar profil Chrome:")
    for index, profile in enumerate(profiles, start=1):
        process_ids = running_profiles.get(profile.name, [])
        status = (
            f"Chrome terbuka, PID {', '.join(map(str, process_ids))}"
            if process_ids
            else "Chrome tertutup; sesi dicek saat Grab"
        )
        print(f"  {index}. {profile.name} [{status}]")
    return profiles


def _first_visible_locator(page: Any, selectors: list[str]) -> Optional[Any]:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible():
                return locator
        except Exception:
            continue
    return None


def _click_first_visible(page: Any, selectors: list[str]) -> bool:
    locator = _first_visible_locator(page, selectors)
    if not locator:
        return False
    try:
        locator.click(timeout=4000)
        return True
    except Exception:
        return False


def _page_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return ""


def _chat_ready(page: Any) -> bool:
    return _first_visible_locator(page, [
        "rich-textarea",
        'div.ql-editor[contenteditable="true"]',
        'textarea[placeholder*="Ask Gemini" i]',
        'textarea[placeholder*="Tanya Gemini" i]',
        'textarea[aria-label*="prompt" i]',
    ]) is not None


def _session_active(page: Any) -> bool:
    if not _chat_ready(page) or _is_login_prompt(page):
        return False
    return _first_visible_locator(page, [
        'button[aria-label*="Google Account" i]',
        'button[aria-label*="Akun Google" i]',
        '[aria-label*="Google Account" i]',
        '[aria-label*="Akun Google" i]',
        'a[href*="SignOutOptions"]',
        "[data-email]",
    ]) is not None


def _is_login_prompt(page: Any) -> bool:
    if "accounts.google.com" in page.url.lower():
        return True
    return _first_visible_locator(page, [
        'button:has-text("Sign in")',
        'a:has-text("Sign in")',
        'button:has-text("Masuk")',
        'a:has-text("Masuk")',
    ]) is not None


def _is_terms_screen(page: Any) -> bool:
    text = _page_text(page)
    return any(term in text for term in (
        "terms of service", "persyaratan layanan", "i agree", "saya setuju",
        "i understand", "saya mengerti",
    ))


def _confirm_terms(page: Any) -> bool:
    clicked = _click_first_visible(page, [
        'button:has-text("I agree")', 'button:has-text("Saya setuju")',
        'button:has-text("I understand")', 'button:has-text("Saya mengerti")',
        'button:has-text("Continue")', 'button:has-text("Lanjutkan")',
        'button:has-text("Next")', 'button:has-text("Berikutnya")',
    ])
    if clicked:
        print("Persetujuan Google dikonfirmasi otomatis.")
        page.wait_for_timeout(1200)
    else:
        print("Dialog persetujuan terdeteksi, tetapi tombol lanjut tidak dikenali.")
    return clicked


def _login_google(page: Any, email: str, password: str) -> bool:
    if not email or not password:
        return False

    if "accounts.google.com" not in page.url.lower():
        page.goto(GOOGLE_SIGN_IN_URL, wait_until="domcontentloaded", timeout=45000)

    email_submitted = False
    password_submitted = False
    terms_approved = False
    phone_notice_shown = False
    otp_attempts = 0
    deadline = time.monotonic() + 240

    while time.monotonic() < deadline:
        if page.is_closed():
            print("Jendela Chrome ditutup sebelum login selesai.")
            password = ""
            return False

        url = page.url.lower()
        if "gemini.google.com" in url and _session_active(page):
            password = ""
            return True

        if _is_terms_screen(page) and not terms_approved:
            terms_approved = True
            if not _confirm_terms(page):
                password = ""
                return False
            continue

        account_choice = None
        try:
            if email:
                account_choice = page.locator("[data-identifier]").filter(has_text=email).first
                if not account_choice.is_visible():
                    account_choice = None
        except Exception:
            account_choice = None
        if account_choice:
            try:
                account_choice.click(timeout=4000)
                page.wait_for_timeout(1000)
                continue
            except Exception:
                pass
        if _click_first_visible(page, [
            'button:has-text("Use another account")',
            'button:has-text("Gunakan akun lain")',
            'button:has-text("Tambahkan akun")',
        ]):
            page.wait_for_timeout(800)
            continue

        email_input = _first_visible_locator(page, [
            "#identifierId", 'input[name="identifier"]', 'input[type="email"]',
            'input[autocomplete="username"]',
        ])
        password_input = _first_visible_locator(page, [
            'input[name="Passwd"]', 'input[name="password"]', 'input[type="password"]',
            'input[autocomplete="current-password"]',
        ])
        code_input = _first_visible_locator(page, [
            "#idvPin", 'input[name="totpPin"]', 'input[name="pin"]',
            'input[autocomplete="one-time-code"]',
        ])

        if code_input:
            if otp_attempts >= 3:
                print("Batas percobaan kode verifikasi tercapai.")
                password = ""
                return False
            code = getpass.getpass("Kode verifikasi Google (tidak ditampilkan): ").strip()
            if not code:
                password = ""
                return False
            code_input.fill(code)
            code_input.press("Enter")
            code = ""
            otp_attempts += 1
            page.wait_for_timeout(1800)
            continue

        body = _page_text(page)
        if any(message in body for message in (
            "wrong password", "incorrect password", "kata sandi salah",
            "couldn't find your google account", "could not find your google account",
            "akun google anda tidak ditemukan",
        )):
            print("Google menolak email atau kata sandi. Periksa input di browser lalu coba lagi.")
            password = ""
            return False
        if "something went wrong" in body or "terjadi kesalahan" in body or "ada yang salah" in body:
            if _click_first_visible(page, [
                'button:has-text("Try again")', 'button:has-text("Coba lagi")',
                'button:has-text("Retry")', 'button:has-text("Ulangi")',
            ]):
                email_submitted = False
                password_submitted = False
                page.wait_for_timeout(1500)
                continue
            print("Google menolak atau gagal memproses login. Periksa halaman Chrome.")
            password = ""
            return False

        if email_input and not email_submitted:
            email_input.fill(email)
            if not _click_first_visible(page, [
                "#identifierNext button", "#identifierNext", 'button:has-text("Next")',
                'button:has-text("Berikutnya")', 'button:has-text("Selanjutnya")',
            ]):
                email_input.press("Enter")
            email_submitted = True
            page.wait_for_timeout(1000)
            continue

        if password_input and not password_submitted:
            password_input.fill(password)
            if not _click_first_visible(page, [
                "#passwordNext button", "#passwordNext", 'button:has-text("Next")',
                'button:has-text("Berikutnya")', 'button:has-text("Masuk")',
            ]):
                password_input.press("Enter")
            password_submitted = True
            page.wait_for_timeout(1500)
            continue

        challenge = any(part in url for part in ("challenge", "signinoptions", "two-step-verification"))
        if challenge:
            if not phone_notice_shown:
                print("Google meminta verifikasi tambahan. Selesaikan di Chrome atau setujui dari ponsel.")
                phone_notice_shown = True
            page.wait_for_timeout(1000)
            continue

        if "myaccount.google.com" in url or ("gemini.google.com" in url and _session_active(page)):
            password = ""
            return True

        if email_submitted and password_submitted and not password_input:
            page.wait_for_timeout(700)
            continue

        page.wait_for_timeout(500)

    password = ""
    print("Waktu login Google habis. Profil Chrome tetap dipertahankan.")
    return False


def _finish_gemini_onboarding(page: Any) -> bool:
    action_selectors = [
        'button:has-text("Use Gemini")', 'button:has-text("Gunakan Gemini")',
        'button:has-text("Try Gemini")', 'button:has-text("Coba Gemini")',
        'button:has-text("Continue")', 'button:has-text("Lanjutkan")',
        'button:has-text("Get started")', 'button:has-text("Mulai")',
        'button:has-text("Chat with Gemini")', 'a:has-text("Chat with Gemini")',
        'button:has-text("Dismiss")', 'button:has-text("Close")', 'button:has-text("Tutup")',
    ]
    deadline = time.monotonic() + 45
    terms_approved = False
    while time.monotonic() < deadline:
        if page.is_closed():
            return False
        if _chat_ready(page):
            return True
        if _is_terms_screen(page) and not terms_approved:
            terms_approved = True
            if not _confirm_terms(page):
                return False
            continue
        if _click_first_visible(page, action_selectors):
            page.wait_for_timeout(1200)
            continue
        page.wait_for_timeout(800)
    return _chat_ready(page)


def _enable_extensions_developer_mode(page: Any) -> bool:
    try:
        page.goto("chrome://extensions/", wait_until="domcontentloaded", timeout=15000)
        toggle = page.locator("extensions-manager extensions-toolbar #devMode")
        toggle.wait_for(state="visible", timeout=10000)
        if toggle.get_attribute("aria-checked") != "true":
            toggle.click(timeout=5000)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if toggle.get_attribute("aria-checked") == "true":
                    break
                page.wait_for_timeout(100)
        if toggle.get_attribute("aria-checked") != "true":
            raise RuntimeError("Developer mode toggle tidak berubah ke posisi aktif.")
        print("Developer mode extension berhasil diaktifkan.")
        return True
    except Exception as exc:
        print(f"Gagal mengaktifkan Developer mode dari halaman Chrome: {exc}", file=sys.stderr)
        return False
    finally:
        if not page.is_closed() and page.url.startswith("chrome://extensions"):
            try:
                page.goto(DEFAULT_URL, wait_until="domcontentloaded", timeout=15000)
            except Exception as exc:
                print(f"Developer mode diproses, tetapi Gemini gagal dibuka kembali: {exc}", file=sys.stderr)


def grab_profile(
    profiles_dir: Path,
    profile_id: str,
    chrome: Optional[Path] = None,
    dry_run: bool = False,
    force_login: bool = False,
) -> int:
    try:
        profile_dir = get_profile_dir(profiles_dir, profile_id)
        running = find_running_profile_processes(profiles_dir)
    except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Tidak bisa memeriksa profil: {exc}", file=sys.stderr)
        return 1

    if profile_id in running:
        print(f"Profil '{profile_id}' sedang dipakai Chrome (PID {', '.join(map(str, running[profile_id]))}).")
        print("Tutup dulu lewat menu 4, lalu jalankan Grab lagi. Sesi profil tidak akan dihapus.")
        return 1

    chrome_executable = chrome.expanduser().resolve() if chrome else find_chrome()
    if not chrome_executable or not chrome_executable.is_file():
        print("Google Chrome tidak ditemukan.", file=sys.stderr)
        return 2

    if dry_run:
        print(f"Mode uji: Grab akan memakai profil persisten '{profile_id}' di {profile_dir}.")
        if force_login:
            print("Profil baru akan diarahkan ke login Google; kredensial diminta saat formulirnya muncul.")
        else:
            print("Sesi aktif akan diperiksa lewat tampilan Gemini; cookie/token tidak dibaca atau diekspor.")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except (OSError, ImportError) as exc:
        print(f"Playwright tidak siap: {exc}", file=sys.stderr)
        return 1

    email = input("Email Google: ").strip()
    if not email:
        print("Email kosong; Chrome tidak dibuka.")
        return 1
    password = getpass.getpass("Password Google (tidak ditampilkan): ")
    if not password:
        print("Password kosong; Chrome tidak dibuka.")
        return 1

    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Gagal menyiapkan folder profil: {exc}", file=sys.stderr)
        password = ""
        return 1

    context = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                executable_path=str(chrome_executable),
                headless=False,
                viewport=None,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            print(f"Grab dimulai dengan profil '{profile_id}'.")
            try:
                target_url = GOOGLE_SIGN_IN_URL if force_login else DEFAULT_URL
                page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                print("Navigasi awal lambat; memeriksa kondisi halaman yang terbuka.")

            if not force_login:
                print("Memeriksa sesi Gemini dari tampilan halaman...")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not page.is_closed():
                    if _session_active(page) or _is_login_prompt(page):
                        break
                    page.wait_for_timeout(500)

            if not force_login and _session_active(page):
                print("Sesi Google/Gemini aktif pada profil ini; kredensial tidak dikirim dan sesi dipakai ulang.")
            else:
                login_detected = force_login or _is_login_prompt(page) or _chat_ready(page)
                if login_detected:
                    _click_first_visible(page, [
                        'button:has-text("Sign in")', 'a:has-text("Sign in")',
                        'button:has-text("Masuk")', 'a:has-text("Masuk")',
                    ])
                    page.wait_for_timeout(1000)
                if not login_detected and "accounts.google.com" not in page.url.lower():
                    print("Status sesi belum dapat dipastikan dari tampilan Gemini; kredensial tidak diminta.")
                    input("Periksa jendela Chrome. Tekan Enter untuk menutup dan kembali ke menu...")
                    return 1
                if "accounts.google.com" not in page.url.lower():
                    page.goto(GOOGLE_SIGN_IN_URL, wait_until="domcontentloaded", timeout=45000)

                if not _login_google(page, email, password):
                    print("Login belum selesai. Profil tetap disimpan tanpa logout atau pembersihan.")
                    input("Tekan Enter untuk menutup Chrome dan kembali ke menu...")
                    return 1

                try:
                    page.goto(DEFAULT_URL, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    print("Navigasi ke Gemini lambat; menunggu tampilan chat.")

                if not _finish_gemini_onboarding(page):
                    print("Login Google terdeteksi, tetapi chat Gemini belum siap. Profil tetap dipertahankan.")
                    input("Tekan Enter untuk menutup Chrome dan kembali ke menu...")
                    return 1
                print("Login berhasil. Gemini siap digunakan.")

            if not _enable_extensions_developer_mode(page):
                print("Login Gemini selesai, tetapi Developer mode belum aktif. Aktifkan manual lewat chrome://extensions.")
                return 1
            input("Tekan Enter untuk menutup Chrome dan kembali ke menu. Sesi profil tetap tersimpan...")
            return 0
    except KeyboardInterrupt:
        print("Grab dihentikan. Profil dan sesi Chrome tetap dipertahankan.")
        return 130
    except Exception as exc:
        print(f"Grab gagal: {exc}", file=sys.stderr)
        print("Tidak ada perintah logout atau penghapusan profil yang dijalankan.")
        return 1
    finally:
        email = ""
        password = ""
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def run_grab_menu(args: argparse.Namespace) -> int:
    while True:
        print("\n=== MENU GRAB GEMINI ===")
        print("1. Grab dengan profil yang sudah ada")
        print("2. Buat profil Chrome baru lalu Grab")
        print("3. Daftar profil dan status Chrome")
        print("4. Hapus profil")
        print("0. Kembali ke menu profil")
        choice = input("Pilih menu Grab: ").strip()

        if choice == "0":
            return 0
        if choice == "1":
            profiles = show_profiles(args.profiles_dir)
            if not profiles:
                continue
            selection = input("Pilih nomor profil untuk Grab: ").strip()
            if not selection.isdigit() or not 1 <= int(selection) <= len(profiles):
                print("Pilihan profil tidak valid.")
                continue
            grab_profile(args.profiles_dir, profiles[int(selection) - 1].name, args.chrome, args.dry_run)
            continue
        if choice == "2":
            profile_id = input("Nama/ID profil baru: ").strip()
            try:
                if get_profile_dir(args.profiles_dir, profile_id).exists():
                    print(f"Profil '{profile_id}' sudah ada. Pilih menu 1 untuk memakai profil itu.")
                    continue
                if args.dry_run:
                    print(f"Mode uji: profil baru '{profile_id}' akan dibuat.")
                else:
                    create_profile(args.profiles_dir, profile_id)
                grab_profile(args.profiles_dir, profile_id, args.chrome, args.dry_run, force_login=True)
            except (ValueError, OSError) as exc:
                print(f"Gagal membuat profil: {exc}")
            continue
        if choice == "3":
            profiles = show_profiles(args.profiles_dir)
            if profiles:
                print("Status sesi login dipastikan saat profil dipilih untuk Grab, bukan dari pembacaan cookie.")
            continue
        if choice == "4":
            profiles = show_profiles(args.profiles_dir)
            if not profiles:
                continue
            print("Pilih nomor profil untuk dihapus, atau A untuk menghapus semua.")
            selection = input("Pilihan: ").strip()
            delete_all = selection.casefold() == "a"
            if delete_all:
                selected_profiles = profiles
            elif selection.isdigit() and 1 <= int(selection) <= len(profiles):
                selected_profiles = [profiles[int(selection) - 1]]
            else:
                print("Pilihan profil tidak valid.")
                continue
            delete_profiles(args.profiles_dir, selected_profiles, args.dry_run, delete_all)
            continue
        print("Pilihan menu tidak dikenal.")


def run_menu(args: argparse.Namespace) -> int:
    while True:
        print("\n=== MENU PROFIL CHROME ===")
        print("1. Buat Profile")
        print("2. Daftar Profile Chrome")
        print("3. Buka Profile")
        print("4. Tutup/Kill Profile Chrome")
        print("5. Delete Profile")
        print("6. Grab Gemini")
        print("0. Keluar")
        choice = input("Pilih menu: ").strip()

        if choice == "0":
            print("Selesai.")
            return 0
        if choice == "1":
            profile_id = input("Masukkan ID/nama profile: ").strip()
            try:
                create_profile(args.profiles_dir, profile_id)
            except ValueError as exc:
                print(exc)
            except OSError as exc:
                print(f"Gagal membuat folder profil: {exc}")
            continue
        if choice == "2":
            show_profiles(args.profiles_dir)
            continue
        if choice == "3":
            profiles = show_profiles(args.profiles_dir)
            if not profiles:
                continue
            selection = input("Pilih nomor profile yang mau dibuka: ").strip()
            if not selection.isdigit() or not 1 <= int(selection) <= len(profiles):
                print("Pilihan profile tidak valid.")
                continue

            profile_id = profiles[int(selection) - 1].name
            target = input("Target [Enter = Gemini, 2 = kosong, atau ketik URL]: ").strip()
            if target == "2":
                target_url = None
            elif not target:
                target_url = DEFAULT_URL
            else:
                target_url = target
            open_profile(args.chrome, args.profiles_dir, profile_id, target_url, args.dry_run)
            continue
        if choice == "4":
            profiles = show_profiles(args.profiles_dir, running_only=True)
            if not profiles:
                continue
            selection = input("Pilih nomor profile Chrome yang mau ditutup: ").strip()
            if not selection.isdigit() or not 1 <= int(selection) <= len(profiles):
                print("Pilihan profile tidak valid.")
                continue

            profile_id = profiles[int(selection) - 1].name
            close_profile(args.profiles_dir, profile_id, args.dry_run)
            continue
        if choice == "5":
            profiles = show_profiles(args.profiles_dir)
            if not profiles:
                continue

            print("Pilih nomor profile yang mau dihapus, atau A untuk menghapus semua.")
            selection = input("Pilihan: ").strip()
            delete_all = selection.casefold() == "a"
            if delete_all:
                selected_profiles = profiles
            elif selection.isdigit() and 1 <= int(selection) <= len(profiles):
                selected_profiles = [profiles[int(selection) - 1]]
            else:
                print("Pilihan profile tidak valid.")
                continue

            delete_profiles(args.profiles_dir, selected_profiles, args.dry_run, delete_all)
            continue
        if choice == "6":
            run_grab_menu(args)
            continue

        print("Pilihan menu tidak dikenal.")


def main() -> int:
    args = parse_args()
    if args.profile_id is None:
        return run_menu(args)
    return open_profile(args.chrome, args.profiles_dir, args.profile_id, args.url, args.dry_run)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)