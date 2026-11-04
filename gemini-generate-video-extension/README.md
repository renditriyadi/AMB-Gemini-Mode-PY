# AMB Gemini & Flow Extension

Extension Manifest V3 yang menghubungkan provider Gemini dan Google Flow ke profil Chrome yang dipilih di AMB. Google Flow dikendalikan melalui UI halaman oleh content script extension; provider Flow tidak memakai CDP.

## Instalasi

Load unpacked pada setiap profil Chrome target:

1. Jalankan **Grab** di AMB untuk profil target. Setelah pendaftaran/login Gemini selesai, AMB membuka `chrome://extensions`, mengaktifkan Developer mode, lalu kembali ke Gemini.
2. Buka `chrome://extensions`, pilih **Load unpacked**, lalu pilih folder ini.
3. Tidak perlu membuka popup, memasukkan ID profil, atau menyimpan kunci.
4. Tutup profil, lalu buka kembali melalui tombol **Buka** di AMB agar pairing otomatis berjalan.

Untuk profil yang sudah dibuat, jalankan Grab sekali saat Chrome untuk profil tersebut tertutup. Developer mode tersimpan di profil dan aktif juga pada pembukaan berikutnya. Jika AMB melaporkan gagal, aktifkan manual melalui `chrome://extensions`.

Setelah memperbarui file extension, buka `chrome://extensions` di profil target dan tekan **Reload** pada **AMB Gemini & Flow** (versi `1.4.0`), setujui izin yang diminta, lalu refresh tab Gemini dan Google Flow yang sudah terbuka. Pastikan extension dimuat dari folder `gemini-generate-video-extension` dalam proyek ini; jangan memilih folder extension Prompt Bridge lama.

Server AMB harus berjalan. Chrome dibuka normal tanpa CDP, termasuk untuk Google Flow. Halaman pairing lokal membawa tiket sekali pakai di URL fragment, yang langsung dihapus dari address bar oleh content script sebelum ditukarkan ke bridge.

Gemini membuat chat baru untuk setiap permintaan dan mengunduh hasil melalui UI Gemini. Google Flow mengatur tipe media, model, rasio, resolusi, dan durasi melalui UI Flow, lalu mengunduh hasil melalui UI Flow. Media referensi Flow juga dikirim dari AMB ke extension melalui endpoint loopback bertoken sementara. AMB menyalin hasil dari folder Downloads ke `media-cache/` dan mencatat jenis media, rasio, serta prompt di riwayat History. Login Google tetap dilakukan manual jika diperlukan.

Untuk desain dan batas keamanan, lihat [PRD Generate Video via Extension](../PRD%20GEMINI%20GENERATE%20VIDEO%20VIA%20EXTENSION.md).
