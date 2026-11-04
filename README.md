# Python Mode Gemini Profile

Folder ini berisi pengelolaan profil Chrome persisten dan prototipe extension bridge Gemini lokal. Generate langsung menggunakan cookie/JSON tidak lagi dipakai.

## Files

- `create_chrome_profile.py` : opens installed Chrome with an isolated persistent profile
- `PRD PYTHON GRAB LOGIN GEMINI PROFILE PERSISTEN.md` : rancangan otomasi login dengan sesi tersimpan di profil Chrome dan tidak diekspor ke Sheet/aplikasi
- `gemini-prompt-bridge-extension/` : Chrome Manifest V3 extension untuk menerima prompt Python pada profil terpilih
- `gemini_prompt_bridge.py` : bridge WebSocket yang hanya bind ke loopback `127.0.0.1`
- `requirements-bridge.txt` : dependensi bridge lokal

Grab memakai Playwright Python dengan Chrome lokal dan profil persisten yang dipilih. Dependensi Playwright dipasang di environment proyek; browser terpisah tidak perlu diunduh.

Catatan: PRD direct-generate lama hanya disimpan sebagai arsip historis. Runner, JSON akun, dan cache runner tersebut sudah dihapus.

## Dedicated Chrome Profile Test

Run the interactive menu:

```powershell
python .\create_chrome_profile.py
```

Menu options: `1` creates a profile, `2` lists managed profiles and shows whether Chrome is open, `3` opens a selected profile, `4` lists only open managed profiles to close, and `5` deletes one selected profile or all profiles with `A`. Login status is checked from the Gemini page when Grab starts. Deletion requires `y/n` confirmation; open profiles are skipped and must be closed from menu `4` first. Detection matches Chrome's exact `--user-data-dir` path against folders in the script's profile directory, so unrelated Chrome windows are not included. After opening Chrome, the script reports success and returns to the menu automatically. Choose `Enter` for Gemini, `2` for a blank tab, or enter another URL.

Menu option `6` opens the Grab submenu. It can reuse a selected profile, create a new profile and start Grab, list profile/browser status, or delete profiles. Grab asks for email and password in the terminal before launching Chrome; if the selected profile is already logged in, those values are not submitted. A successful login remains in the Chrome profile when Grab closes the browser; it is not exported to the application or logs.

## Extension Penghubung Prompt Gemini

Prototipe ini mengirim prompt ke tab Gemini melalui extension yang dipasang pada profil Chrome tujuan. Extension tidak membaca cookie, token, password, atau penyimpanan browser. Bridge hanya menerima koneksi lokal di `127.0.0.1` dan melaporkan apakah prompt terkirim ke UI; bridge belum memastikan video selesai dibuat.

1. Pasang dependensi bridge pada `.venv` proyek:

	```powershell
	python -m pip install -r '.\pythone mode\requirements-bridge.txt'
	```

2. Pada Chrome di profil target, buka `chrome://extensions`, aktifkan **Developer mode**, pilih **Load unpacked**, lalu pilih folder `pythone mode\gemini-prompt-bridge-extension`. Ulangi untuk setiap profil yang akan menerima prompt.
3. Jalankan bridge dari root proyek:

	```powershell
	python '.\pythone mode\gemini_prompt_bridge.py'
	```

4. Salin kunci bridge lokal yang ditampilkan Python ke popup extension pada setiap profil. Isi ID profil agar sama persis dengan nama folder, misalnya `tes6`. Kunci ini hanya untuk bridge lokal, bukan kredensial Google; jangan bagikan ke orang lain.
5. Biarkan bridge berjalan, buka tab Gemini pada profil target, lalu masukkan ID profil dan prompt di terminal Python.

Extension memakai kontrol UI Gemini biasa. Google tetap dapat menolak, menunda, atau gagal membuat video. Status `prompt_sent` hanya memastikan tombol kirim diklik. Kunci bridge disimpan di `%LOCALAPPDATA%\AMB Media V5\gemini-prompt-bridge.key`; hapus berkas itu untuk membuat kunci baru.

For a quick launch without the menu:

```powershell
python .\create_chrome_profile.py --profile-id gemini-test-01
```

The profile data is stored under `%LOCALAPPDATA%\AMB Media V5\ChromeProfiles`, outside the project folder. Profile names may contain spaces, for example `tes rendi1`. Sign in to Gemini manually the first time. Reuse the same profile name to keep that browser session; use a different name for a separate profile. Do not open the same profile name in multiple Chrome processes at once. Menu option `4` force-closes the selected profile's Chrome process tree, so save any work in that window first.



#Mode CLI di terminal tulis, .\.venv\Scripts\python.exe .\create_chrome_profile.py
