# PRD: Generate Video Gemini lewat Extension Lokal

## Status

Implementasi MVP selesai; validasi dengan profil Chrome/Gemini pengguna masih perlu dilakukan.

## Ringkasan

Mengganti pendekatan CDP dengan extension Chrome baru untuk mengirim prompt ke UI Gemini pada profil yang dipilih di AMB. Pengguna memasang extension unpacked satu kali pada setiap profil Chrome yang akan dipakai. Extension tidak meminta ID profil, kunci, URL server, email, atau pengaturan apa pun di popup.

Extension lama di `gemini-prompt-bridge-extension/` tidak diubah, dipindahkan, atau dihapus.

## Masalah

CDP mengaktifkan remote debugging pada Chrome. Pengujian pengguna menunjukkan Gemini merespons saat Chrome dibuka tanpa CDP, tetapi tidak merespons saat CDP aktif. Karena itu, alur Generate perlu dicoba melalui extension di Chrome normal tanpa remote-debugging port.

## Tujuan

- Mengirim prompt melalui composer dan tombol kirim UI Gemini pada tab Chrome normal.
- Memilih target dari daftar profil Chrome yang sudah dikenali AMB.
- Tidak meminta pengguna mengisi pengaturan pada extension.
- Menjaga koneksi lokal AMB-extension pada loopback.
- Memisahkan implementasi baru dari extension Prompt Bridge lama.

## Bukan tujuan

- Menyamarkan otomasi atau menghindari mekanisme deteksi situs.
- Membaca/mengekspor cookie, token, password, atau local storage.
- Memanggil API/RPC internal Gemini.
- Menjamin Google menerima prompt, menyediakan Veo, atau menyelesaikan video.
- Mengunduh hasil video pada MVP.

## Pengalaman pengguna

1. Pengguna menjalankan server AMB dan membuka tab **Generate Video**.
2. Pengguna memasang folder extension baru sebagai unpacked extension pada setiap profil Chrome target. Tidak ada konfigurasi/popup yang perlu diisi.
3. Pengguna membuka profil target dari AMB. Chrome berjalan normal tanpa flag remote debugging.
4. AMB memasangkan koneksi extension dengan ID profil yang sedang dibuka melalui tiket pairing lokal sekali pakai. Extension menyimpan identitas pairing pada `chrome.storage.local`; tidak menyimpan kredensial Google.
5. Pengguna login ke Gemini secara manual dan memilih mode Video/Veo bila tersedia.
6. Pengguna memilih profil di Generate Video, memasukkan prompt, lalu mengirimnya.
7. AMB menampilkan status dari extension: diterima, composer tidak ditemukan, prompt dikirim, error UI yang dikenali, atau timeout. Status “prompt dikirim” tidak sama dengan video selesai.

## Rancangan pairing tanpa konfigurasi extension

Extension tidak dapat membaca nama folder profil Chrome atau mengetahui ID profil AMB dengan sendirinya. Karena itu, pemasangan extension saja tidak cukup untuk mengaitkan koneksi ke nama profil.

MVP menggunakan pairing otomatis saat profil dibuka melalui AMB:

1. AMB membuat token acak sekali pakai, terikat ke ID profil dan kedaluwarsa dalam waktu singkat.
2. AMB membuka Chrome tanpa CDP ke halaman pairing lokal AMB yang memuat token tersebut.
3. Content script extension hanya membaca token dari halaman pairing lokal yang tepat, mengirimkannya ke service worker, lalu service worker menukarkannya ke server lokal.
4. Server memvalidasi token, mengaitkan koneksi extension dengan profil target, lalu halaman pairing mengarahkan pengguna ke Gemini.
5. Extension menyimpan ID pairing lokal dan menghubungkan ulang secara otomatis jika service worker dihentikan/restart. Pairing ulang dilakukan lewat tombol Buka profil di AMB.

Tidak ada token pairing statis atau kunci yang ditampilkan/diisi pengguna. Token sekali pakai tidak boleh diterima dari klien selain pada alur pairing, tidak dicatat ke log, dan tidak dapat digunakan untuk profil lain.

Jika pairing berbasis halaman lokal terbukti tidak stabil, alternatif yang perlu disetujui adalah tombol **Pasangkan** pada AMB yang meminta pengguna memilih profil di UI AMB; tetap tanpa input di popup extension.

## Arsitektur

- Folder extension baru: `gemini-generate-video-extension/`.
- Manifest V3 dengan service worker untuk koneksi WebSocket/komunikasi AMB dan content script terbatas ke halaman pairing lokal serta `https://gemini.google.com/*`.
- Server AMB menjadi otoritas pairing dan pemetaan `profile_id` ke koneksi extension.
- Endpoint bridge baru menggunakan port lokal terpisah dari extension lama, bind hanya ke `127.0.0.1`.
- UI Generate hanya menampilkan profil yang terbuka dan telah paired.
- Pesan bridge memiliki tipe, ID request, batas ukuran, timeout, dan hasil status eksplisit.
- Server tidak menerima URL WebSocket, profile ID arbitrer, atau token pairing permanen dari browser UI.

## Persyaratan fungsional

### Extension

- Tidak ada popup konfigurasi, form ID profil, atau kolom kunci.
- Hanya menerima instruksi generate untuk tab Gemini dalam profil paired.
- Memastikan target adalah `https://gemini.google.com/` dan composer terlihat sebelum mengisi.
- Mengisi composer memakai event UI yang diperlukan lalu mengirim melalui tombol UI yang dikenali.
- Tidak mengirim ulang otomatis setelah status ambigu, timeout, atau koneksi putus.
- Mengabaikan command dengan profil/request ID yang tidak cocok.
- Reconnect dengan backoff dan melaporkan status koneksi; tidak retry pengiriman prompt secara diam-diam.

### AMB

- Aksi Buka profil tidak menambahkan `--remote-debugging-address` atau `--remote-debugging-port`.
- State menampilkan status extension: belum dipasang/terhubung, pairing, siap, sibuk, atau error.
- Generate memvalidasi ID profil, prompt kosong, dan batas 5.000 karakter.
- Prompt diarahkan hanya ke koneksi extension yang dipetakan ke profil terpilih.
- Job mendukung progres, timeout, dan pembatalan; membatalkan tidak menutup Chrome atau menghapus pairing.
- Riwayat tidak menyimpan isi prompt secara default; log tidak berisi token pairing.

## Keamanan dan privasi

- Server HTTP/WebSocket hanya bind pada loopback; Host dan Origin divalidasi.
- Pairing memakai token acak sekali pakai, masa berlaku singkat, dan satu profil tujuan.
- Token pairing tidak masuk query log, console extension, atau riwayat job.
- Tidak membaca cookie, token Google, password, local storage, atau isi percakapan yang tidak diperlukan.
- Content script dibatasi pada domain Gemini dan halaman pairing lokal.
- Extension tidak mengubah user-agent, fingerprint, atau perilaku untuk menghindari deteksi.
- Extension tidak menggunakan `externally_connectable` atau akses host yang tidak diperlukan.

## Batasan dan risiko

- Gemini dapat mengubah DOM, composer, label tombol, atau alur Video/Veo.
- Extension tetap mengotomasi UI; keberhasilan dan kebijakan layanan Google tidak dijamin.
- Chrome dapat menangguhkan service worker; reconnect dan state harus tahan restart.
- Hubungan tab/profil tidak dapat diturunkan dari ID extension saja; pairing AMB wajib sebelum profil tampil sebagai target.
- Pairing page membuka satu navigasi awal tambahan sebelum Gemini.

## Kriteria penerimaan

- Extension lama tidak berubah; extension baru berada seluruhnya di folder baru.
- Extension dapat dipasang pada beberapa profil tanpa mengisi popup atau file konfigurasi.
- Profil yang dibuka oleh CLI tanpa pairing tidak keliru dikenali sebagai target AMB.
- Profil yang dibuka lewat AMB tanpa CDP terpasangkan ke ID profil yang benar.
- Generate pada profil A tidak pernah mengirim prompt ke profil B.
- Extension mengirim prompt lewat UI Gemini saja dan melaporkan status pengiriman secara jujur.
- Tidak ada remote-debugging port, cookie/token Google, atau RPC internal Gemini yang digunakan.
- Tes mencakup validasi pairing, reconnect, routing multi-profil, batas input, timeout, pembatalan, dan Origin.

## Tahapan

1. Selesai: pairing otomatis memakai tiket sekali pakai pada URL fragment halaman lokal.
2. Selesai: service WebSocket baru di `gemini_generate_bridge.py`, port loopback 8766.
3. Selesai: extension MV3 baru di `gemini-generate-video-extension/`; extension lama tidak diubah.
4. Selesai: aksi Buka membuat pairing, status koneksi ditampilkan, dan tab Generate merutekan prompt ke profil terpilih.
5. Selesai: tes unit/API dan validasi sintaks; smoke test Gemini langsung dilakukan pengguna karena perlu login dan interaksi akun.
