# Python Mode UI

File lama (`create_chrome_profile.py`, `gemini_prompt_bridge.py`, extension) tidak diubah.
`server.py` memanggil fungsi yang sama dan mengalihkan `print`/`input`/`getpass` ke UI.

## Jalankan (Windows)
    pip install -r requirements-server.txt
    python server.py            # buka http://127.0.0.1:8000
Opsi: `--port`, `--profiles-dir`, `--chrome`, `--dry-run`, `--no-browser`.

## Isi
- Tab Profil Chrome: buat profil baru + Grab, Grab profil lama, Buka, Tutup, Hapus (satu/semua).
- Email/password Grab diisi di form. Email disimpan per profil di `python-mode-email.txt` agar tampil kecil di UI setelah restart; password hanya hidup di memori selama proses dan tidak disimpan aplikasi. Kode OTP muncul sebagai kolom input di panel log.
- Tab Prompt Bridge: jalankan/hentikan bridge, lihat kunci lokal, kirim prompt ke profil yang extension-nya terhubung, riwayat status.

## Keamanan
Server hanya bind ke 127.0.0.1, menolak Host selain localhost, dan semua /api butuh token per-run yang ditanam di halaman.
