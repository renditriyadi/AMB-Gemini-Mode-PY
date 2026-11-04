# AMB Gemini Generate Video Extension

Extension Manifest V3 terpisah untuk mengirim prompt ke UI Gemini melalui profil Chrome yang dipilih di AMB. Extension Prompt Bridge lama di `gemini-prompt-bridge-extension/` tidak diubah.

## Instalasi

Load unpacked pada setiap profil Chrome target:

1. Jalankan **Grab** di AMB untuk profil target. Setelah pendaftaran/login Gemini selesai, AMB membuka `chrome://extensions`, mengaktifkan Developer mode, lalu kembali ke Gemini.
2. Buka `chrome://extensions`, pilih **Load unpacked**, lalu pilih folder ini.
3. Tidak perlu membuka popup, memasukkan ID profil, atau menyimpan kunci.
4. Tutup profil, lalu buka kembali melalui tombol **Buka** di AMB agar pairing otomatis berjalan.

Untuk profil yang sudah dibuat, jalankan Grab sekali saat Chrome untuk profil tersebut tertutup. Developer mode tersimpan di profil dan aktif juga pada pembukaan berikutnya. Jika AMB melaporkan gagal, aktifkan manual melalui `chrome://extensions`.

Setelah memperbarui file extension, buka `chrome://extensions` di profil target dan tekan **Reload** pada **AMB Gemini Generate Video** (versi terbaru `1.3.1`), setujui izin akses webRequest jika diminta, lalu refresh tab Gemini. Pastikan extension dimuat dari folder `gemini-generate-video-extension` dalam proyek ini; jangan memilih folder extension Prompt Bridge lama.

Server AMB harus berjalan. Chrome dibuka normal tanpa CDP. Halaman pairing lokal membawa tiket sekali pakai di URL fragment, yang langsung dihapus dari address bar oleh content script sebelum ditukarkan ke bridge.

Setiap permintaan membuka chat Gemini baru agar hasil tidak tertukar. Extension mengirim prompt melalui composer, lalu memakai tombol Download Gemini. Extension juga mengenali URL unduhan langsung `googleusercontent.com/rd-gg-dl/` sebagai fallback jika unduhan dari tombol tidak tercatat. AMB menyalin hasil dari folder Downloads ke `media-cache/` di folder proyek dan mencatat ID chat, jenis media, rasio, serta prompt di riwayat History. URL bertanda tangan hanya dipakai sementara untuk unduhan dan tidak disimpan dalam riwayat. Login dan mode Video/Veo tetap dilakukan manual jika diperlukan.

Untuk desain dan batas keamanan, lihat [PRD Generate Video via Extension](../PRD%20GEMINI%20GENERATE%20VIDEO%20VIA%20EXTENSION.md).
