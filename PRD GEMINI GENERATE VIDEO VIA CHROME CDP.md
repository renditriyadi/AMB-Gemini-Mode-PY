# PRD Arsip: Generate Video Gemini lewat Chrome CDP

## Status

Disupersesi oleh [PRD Generate Video via Extension](./PRD%20GEMINI%20GENERATE%20VIDEO%20VIA%20EXTENSION.md). Jalur CDP tidak lagi dipakai oleh aplikasi karena pengujian pengguna menunjukkan Gemini tidak merespons saat Chrome dibuka dengan CDP aktif.

## Ringkasan

AMB Mode mengirim prompt video ke Gemini melalui tab Chrome asli pada profil yang dipilih, lalu memantau hasil render dari tampilan halaman tersebut. Fitur ini tidak memakai extension, Playwright, cookie yang diekspor, atau endpoint internal Gemini.

## Masalah

Prompt Bridge yang ada bergantung pada extension dan hanya memastikan tombol kirim diklik. Pengguna ingin memilih profil Chrome yang dibuka AMB, mengirim prompt video ke Gemini, dan melihat apakah hasilnya sedang dibuat atau sudah selesai tanpa memasang extension.

## Tujuan

- Mengirim prompt melalui UI Gemini yang terlihat pada Chrome asli.
- Memakai sesi login yang sudah tersimpan pada profil Chrome terkelola.
- Menampilkan progres, keberhasilan, kegagalan, pembatalan, dan timeout melalui log job AMB.
- Membatasi kendali browser ke profil yang dibuka melalui tombol **Buka** di AMB.

## Bukan tujuan MVP

- Mengambil, membaca, mengekspor, atau menyimpan cookie/token Google.
- Memanggil RPC atau endpoint Gemini internal, termasuk endpoint riwayat percakapan.
- Menggunakan Playwright, Selenium, atau extension Chrome.
- Mengunduh atau mengelola file video hasil. Fitur unduh dapat direncanakan terpisah setelah deteksi hasil tervalidasi.
- Menjamin ketersediaan Veo, kuota akun, waktu render, atau keberhasilan prompt.

## Pengguna dan alur utama

1. Pengguna membuka profil dari kartu profil AMB memakai **Buka**. Chrome dijalankan dengan remote debugging pada loopback.
2. Pengguna login ke Gemini secara manual jika diperlukan.
3. Pengguna membuka tab **Generate Video**, memilih profil aktif yang didukung, dan memasukkan prompt.
4. AMB membuka atau memakai tab Gemini pada profil itu, mengisi prompt melalui Chrome DevTools Protocol (CDP), lalu menekan tombol kirim di UI.
5. AMB memantau perubahan UI secara berkala dan melaporkan status hingga video terlihat, terjadi error yang dikenali, job dibatalkan, atau batas waktu tercapai.

Profil Chrome yang sudah terbuka tanpa CDP harus ditutup dan dibuka ulang dari AMB sebelum dapat dipilih. Profil yang sedang tertutup, sedang diproses, atau tidak mempunyai port CDP aktif tidak dapat menjadi target.

## Persyaratan fungsional

### Pembukaan dan pemilihan profil

- Hanya aksi **Buka** dari server AMB yang menambahkan `--remote-debugging-address=127.0.0.1` dan `--remote-debugging-port=0`.
- Chrome memilih port dinamis dan menulis `DevToolsActivePort` pada direktori data profil.
- API state hanya menandai profil sebagai target jika proses profil berjalan dan metadata port valid.
- Server membaca target dan halaman dari port localhost yang ditemukan pada metadata profil; klien tidak menerima alamat CDP arbitrer dari browser UI.
- Jika terdapat beberapa tab Gemini, MVP memakai tab Gemini pertama yang dilaporkan Chrome. Jika tidak ada, AMB membuat tab Gemini baru pada profil itu.

### Pengiriman prompt

- Prompt wajib tidak kosong dan maksimum 5.000 karakter.
- Sebelum mengirim, AMB memastikan tab berada pada `gemini.google.com`, kolom prompt terlihat, dan tombol kirim siap.
- Prompt dimasukkan melalui DOM di tab Chrome dan dikirim menggunakan kontrol UI yang terlihat.
- Log “prompt terkirim” hanya berarti klik kirim berhasil; bukan berarti video selesai.
- Operasi gagal dengan pesan yang jelas jika Chrome/CDP, tab, composer, tombol kirim, atau sesi Gemini tidak siap.

### Polling dan hasil

- Polling UI dilakukan tiap 4 detik dengan batas waktu MVP 10 menit.
- Status berjalan ditampilkan pada log job tanpa mengubah status menjadi berhasil sebelum bukti hasil ditemukan.
- Berhasil hanya jika ditemukan elemen video atau tautan video baru yang muncul setelah prompt dikirim.
- Pesan penolakan/kuota/error yang dikenali ditampilkan sebagai gagal, bukan sukses.
- Jika batas waktu tercapai tanpa elemen video baru, status menyatakan video tidak terdeteksi.
- Timeout dan pembatalan tidak menutup Chrome atau menghapus profil/sesi.
- Pembatalan diperiksa setidaknya sebelum setiap siklus polling.

## Persyaratan UI

- Tab **Prompt Bridge** pada UI AMB diganti menjadi **Generate Video** dan tidak membutuhkan extension.
- Pemilih target hanya menampilkan profil yang terbuka dengan CDP aktif.
- Tab menampilkan kolom prompt, batas karakter, tombol **Generate**, keterangan jika tidak ada profil siap, dan riwayat singkat status job.
- Output terperinci, pertanyaan/error, pembatalan, dan timeout memakai panel **Aktivitas** yang sudah ada.
- Tab tema gelap/terang dan tata letak responsif tetap berfungsi.

## Persyaratan keamanan dan privasi

- CDP hanya bind ke `127.0.0.1`, port dinamis, dan menggunakan direktori profil terkelola non-default.
- Jangan menambahkan `--remote-allow-origins=*`, server CDP yang bind ke semua interface, atau endpoint HTTP publik untuk CDP.
- API Generate hanya menerima ID profil tervalidasi dan prompt; jangan menerima URL WebSocket, port, path, cookie, atau token dari klien.
- Jangan membaca atau mencetak isi cookie, local storage, password, token login, atau payload halaman lengkap.
- Ambil hanya informasi DOM yang diperlukan untuk composer, status generasi, pesan error, dan bukti video.
- UI/API AMB tetap terikat ke loopback serta dilindungi token per-run.

## Batasan dan risiko

- Struktur DOM dan label Gemini dapat berubah; perubahan selector memerlukan penyesuaian.
- Tidak semua status render mempunyai indikator yang dapat dibaca secara konsisten.
- Deteksi selesai harus konservatif. Ketiadaan indikator berjalan bukan bukti video selesai.
- Jika port CDP hilang, tab tertutup, Chrome crash, atau sesi tidak login, job harus gagal/timeout dengan penjelasan dan tanpa fallback ke cookie atau extension.
- Aplikasi lokal lain yang berjalan sebagai pengguna Windows yang sama berpotensi mengakses layanan loopback CDP. Jangan membuka profil CDP dari jaringan atau membagikan port debug.

## Rencana implementasi

1. Selesai: modul CDP lokal membaca `DevToolsActivePort`, memilih/membuka target Gemini, menggunakan Runtime.evaluate, dan menutup WebSocket.
2. Selesai: aksi Buka UI mengaktifkan CDP loopback dan state menampilkan kesiapan profil.
3. Selesai: job Generate memvalidasi profil/prompt, memantau DOM, serta mendukung pembatalan, progres, dan timeout.
4. Selesai: tab UI diganti menjadi Generate Video; aset dan endpoint extension lama tetap terpisah.
5. Selesai: dokumentasi penggunaan dan tes unit protokol/API diperbarui. Verifikasi live tetap memerlukan Chrome/Gemini pada mesin pengguna.

## Kriteria penerimaan

- Profil yang dibuka lewat AMB memiliki metadata CDP; profil Chrome biasa tidak tampil sebagai target.
- Tidak ada extension atau Playwright diperlukan untuk mengirim prompt dan polling.
- Pengujian menunjukkan bahwa hanya target loopback dari metadata profil yang diterima; URL eksternal ditolak.
- Prompt terisi dan tombol kirim diklik hanya pada tab Gemini di profil pilihan.
- Hasil tidak dinyatakan sukses hanya berdasarkan teks “prompt terkirim” atau hilangnya indikator generasi.
- Video baru, error/penolakan, timeout, dan pembatalan menghasilkan status yang berbeda dan dapat dibaca.
- Membatalkan job tidak mematikan Chrome dan tidak menghapus sesi.
- Tombol, tema, responsivitas UI, dan fitur profil yang sudah ada tetap bekerja.
- Dokumentasi menyebut batas timeout, kebutuhan membuka ulang profil, serta sifat lokal/berprivilege CDP.

## Rollout

- Implementasi MVP menambahkan fitur Generate tanpa menjalankan otomatisasi pada profil yang sudah terbuka.
- Profil lama yang dibuka sebelum fitur CDP harus ditutup lalu dibuka kembali melalui AMB.
- Extension Prompt Bridge lama tidak digunakan oleh tab Generate dan tidak dihapus dalam perubahan ini.
- Tidak ada migrasi profil maupun perubahan format kredensial.
