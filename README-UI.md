# Python Mode UI

`server.py` menyediakan dashboard lokal untuk pengelolaan profil dan generate gambar/video lewat Gemini atau Google Flow.

## Jalankan (Windows)
    pip install -r requirements-server.txt
    python server.py            # buka http://127.0.0.1:8000 secara manual
Server tidak membuka browser otomatis. Opsi: `--port`, `--profiles-dir`, `--chrome`, `--dry-run`.

## Isi
- Tab Profil Chrome: buat profil baru + Grab, Grab profil lama, Buka, Tutup, Hapus (satu/semua).
- Email/password Grab diisi di form. Email disimpan per profil di `python-mode-email.txt` agar tampil kecil di UI setelah restart; password hanya hidup di memori selama proses dan tidak disimpan aplikasi. Kode OTP muncul sebagai kolom input di panel log.
- Extension baru berada di `gemini-generate-video-extension/`. Setelah pendaftaran/login Gemini saat Grab, Playwright membuka `chrome://extensions`, mengaktifkan Developer mode, lalu kembali ke Gemini. Setelah itu Load unpacked extension satu kali; tidak ada popup atau pengaturan untuk diisi.
- Untuk Gemini, jika profil yang dipilih tertutup, tombol Generate membukanya melalui AMB, memasangkan extension, menunggu koneksi, lalu mengirim prompt. Extension harus sudah dipasang pada profil tersebut.
- Google Flow menggunakan profil yang dibuka dengan tombol **Flow** dan CDP loopback. Di kedua mode output, tombol **+** menerima gambar PNG/JPG/WEBP (maksimal 20 MB) atau video MP4/WEBM/MOV/M4V (maksimal 100 MB). Setelan menentukan jenis output; AMB memastikan ID media yang diunggah sama dengan media yang ditempel ke kotak perintah sebelum menjalankan generate. Jika dialog hak penggunaan muncul saat upload, AMB memilih **Saya setuju** sesuai instruksi pengguna.
- Setiap generate memakai chat Gemini baru. Extension menunggu hasil gambar/video selesai, lalu menggunakan tombol Download Gemini; AMB menyimpan file dan riwayat persisten di `media-cache/`. Tab History menampilkan media, prompt, rasio, profil, dan tautan ke chat.
- Chrome server dibuka tanpa remote debugging/CDP. Jika profil sudah terbuka tetapi extension belum terhubung, Generate menunggu koneksi extension tanpa membuka jendela profil kedua.
- Login ke Gemini manual dan pilih mode Video/Veo di halaman Gemini jika akun memerlukannya.
- Generate memakai Chrome asli melalui extension lokal; tidak membaca cookie atau memanggil API internal Gemini. Playwright tetap menjadi dependensi fitur Grab.
- Tes: `python -m unittest discover -s tests`.

## Keamanan
Server HTTP dan extension bridge hanya bind ke loopback. Server menolak Host selain localhost, semua `/api` butuh token per-run, pairing memakai tiket acak sekali pakai, dan Generate hanya menerima ID profil tervalidasi. Jangan bagikan tiket pairing atau port bridge.
