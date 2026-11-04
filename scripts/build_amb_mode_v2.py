from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "AMB-Mode-V2.zip"

ROOT_FILES = (
    ".gitignore",
    "CARA-INSTALL-DAN-JALANKAN.txt",
    "README-UI.md",
    "README.md",
    "create_chrome_profile.py",
    "flow_generate.py",
    "gemini_generate_bridge.py",
    "gemini_prompt_bridge.py",
    "requirements-bridge.txt",
    "requirements-server.txt",
    "server.py",
)
PROJECT_DIRS = (
    "gemini-generate-video-extension",
    "gemini-prompt-bridge-extension",
    "ui",
)
BUILD_SCRIPT = Path("scripts") / "build_amb_mode_v2.py"


def package_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    for directory in PROJECT_DIRS:
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    files.append(ROOT / BUILD_SCRIPT)

    missing = [path.relative_to(ROOT) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "File paket wajib tidak ditemukan: " + ", ".join(map(str, missing))
        )
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix().casefold())


def build_archive() -> Path:
    files = package_files()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".AMB-Mode-V2-",
            suffix=".tmp",
            dir=ROOT,
            delete=False,
        ) as temporary:
            temp_path = Path(temporary.name)

        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(ROOT).as_posix())

        os.replace(temp_path, OUTPUT)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return OUTPUT


if __name__ == "__main__":
    archive_path = build_archive()
    print(f"ZIP berhasil dibuat: {archive_path}")
    print(f"Ukuran: {archive_path.stat().st_size:,} bytes")
    print(f"File: {len(package_files())}")
