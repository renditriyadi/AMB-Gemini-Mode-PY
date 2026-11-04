# Penghubung Prompt Gemini Python

Extension Chrome Manifest V3 ini menerima perintah prompt dari bridge Python lokal dan meneruskannya ke kolom chat Gemini pada profil Chrome tempat extension dipasang.

## Batas akses

- Hanya mencocokkan halaman `https://gemini.google.com/*`.
- Tidak membaca atau mengirim cookie, token login, password, atau isi profil Chrome.
- Tidak menghubungi API internal Google; prompt dikirim melalui kolom chat dan tombol kirim yang terlihat.
- Status `prompt_sent` berarti tombol kirim berhasil diklik. Status itu belum membuktikan Gemini sudah selesai membuat video.
- Bridge lokal mengikat alamat `127.0.0.1`; kunci bridge bukan kredensial Google dan jangan dibagikan.

## Pasang ke profil Chrome

1. Buka Chrome menggunakan profil yang dituju.
2. Buka `chrome://extensions`.
3. Aktifkan **Developer mode**.
4. Pilih **Load unpacked** dan pilih folder `gemini-prompt-bridge-extension` ini.
5. Ulangi untuk setiap profil Chrome yang akan dipakai. Penyimpanan extension terpisah pada setiap profil.
6. Klik ikon extension lalu pilih **Buka pengaturan di tab**. Di tab pengaturan yang tetap terbuka, isi ID profil yang sama dengan nama folder profil Python dan kunci dari bridge, lalu pilih **Simpan & hubungkan**. Kamu bisa berpindah ke terminal tanpa menutup halaman ini.

## Jalankan bridge

Dari root proyek, jalankan:

```powershell
python '.\pythone mode\gemini_prompt_bridge.py'
```

Bridge menampilkan kunci lokal dan menunggu extension tersambung. Buka Gemini pada profil yang dipasangkan, lalu isi ID profil dan prompt di terminal. Bridge meneruskan perintah hanya ke extension yang mendaftarkan ID profil tersebut.

Kunci dibuat di `%LOCALAPPDATA%\AMB Media V5\gemini-prompt-bridge.key`. Hapus berkas itu untuk merotasi kunci; setelah itu masukkan kunci baru pada popup extension di profil terkait.
