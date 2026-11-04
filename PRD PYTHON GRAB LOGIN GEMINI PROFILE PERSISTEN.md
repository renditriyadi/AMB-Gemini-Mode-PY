# PRD - Python Mode Grab Login Gemini dengan Profil Chrome Persisten

Status: Draft untuk disepakati sebelum implementasi
Tanggal: 2026-11-03
Folder: `pythone mode`
Acuan alur: [GeminiPlaywrightAddAccount.tsx](../src/components/gemini/GeminiPlaywrightAddAccount.tsx) dan [geminiPlaywright.ts](../server/services/geminiPlaywright.ts)

## 1. Ringkasan

Dokumen ini merancang otomasi Python yang mengikuti pola login dan aktivasi Gemini pada Playwright yang sudah ada: membuka browser, mengisi email dan kata sandi saat proses berjalan, mendeteksi layar Google yang berubah-ubah, menunggu atau memproses langkah verifikasi, membuka Gemini, menangani onboarding, lalu memastikan antarmuka chat siap.

Otomatis mengikuti pola berarti menyamakan urutan proses, status, deteksi kondisi, retry terbatas, timeout, pembatalan, dan cleanup. Fitur ini tidak membuat akun Google baru: alur sumber yang dipelajari adalah login akun Google yang sudah ada dan aktivasi/akses Gemini. Sesi login memang harus tersimpan di profil Chrome terpilih agar dapat dipakai pada run berikutnya. Python tidak boleh mengekspor sesi itu ke Sheet, database aplikasi, JSON, log, atau layanan lain.

## 2. Temuan dari Alur Playwright Saat Ini

Alur aplikasi yang dipelajari dan menjadi acuan kesetaraan fungsi Python:

1. Inisialisasi browser dan status sesi.
2. Navigasi ke Google Sign In dan deteksi formulir yang tampil.
3. Isi email secara otomatis, kirim, lalu deteksi account chooser, konfirmasi identitas, halaman error/retry, persetujuan, atau formulir kata sandi.
4. Isi kata sandi dari input sementara saat run, kirim, lalu tunggu konfirmasi login Google melalui kondisi halaman.
5. Deteksi MFA: untuk kode sekali pakai, minta kode saat dibutuhkan dan kirim otomatis tanpa menyimpannya; untuk persetujuan ponsel, tunggu persetujuan pengguna. Jangan mengakali CAPTCHA atau challenge keamanan.
6. Setelah login terkonfirmasi, navigasi ke Gemini.
7. Tangani tombol masuk, Use Gemini, Continue, dan dialog onboarding sesuai bahasa/tampilan. Klik otomatis tombol persetujuan syarat yang dikenali; jangan meminta jawaban y/n di terminal.
8. Ulangi pemeriksaan sampai composer chat terlihat atau batas siklus/timeout tercapai.
9. Laporkan hasil. Saat browser ditutup, pertahankan profil dan sesi login Chrome; jangan logout atau membersihkan data situs.

Implementasi sumber memakai profil browser persisten dan mengekstrak sesi untuk integrasi akun. Python mengikuti penggunaan profil persisten, tetapi mengecualikan ekstraksi dan pengiriman sesi ke integrasi akun/Sheet. Kata sandi juga tidak disimpan; hanya sesi browser yang dikelola Chrome yang dipertahankan.

## 3. Tujuan

- Mengotomatisasi langkah login rutin Google dengan kredensial yang hanya diberikan untuk run saat ini.
- Mengikuti layar perantara Google dan onboarding Gemini seperti state machine Playwright.
- Mendukung input MFA sementara atau persetujuan ponsel oleh pengguna tanpa menyimpan nilainya.
- Memastikan status `ready` hanya muncul setelah composer chat Gemini terdeteksi.
- Memakai ulang sesi login yang sudah ada pada profil Chrome terpilih.
- Mempertahankan profil dan sesi Chrome saat browser ditutup; tidak melakukan logout atau pembersihan otomatis.

## 4. Batas Keamanan dan Privasi

### Wajib

- Sebelum Chrome dibuka, email dan kata sandi diminta di terminal pada setiap run Grab; kata sandi dibaca tersembunyi dengan `getpass`.
- Jika profil ternyata sudah memiliki sesi aktif, kredensial yang baru dimasukkan tidak dikirim; sesi Chrome yang sudah ada langsung dipakai.
- Kata sandi, kode MFA, dan email pemulihan (jika diminta) tidak ditulis ke berkas, database, log, argumen command line, atau variabel lingkungan.
- Python tidak membaca API cookie browser, `document.cookie`, basis data profil Chrome, token, atau header autentikasi. Chrome sendiri mengelola sesi login di dalam profil terpilih.
- Python tidak menyalin atau mengekspor cookie/token ke papan klip, terminal, Sheet, berkas JSON, log, database aplikasi, maupun server.
- Python tidak menyimpan kode MFA, alamat pemulihan, tangkapan layar, atau data isi formulir.
- Browser berjalan dengan profil persisten berdasarkan ID profil yang dipilih atau baru dibuat. Data profil tidak dihapus saat browser ditutup.
- Skrip tidak menekan tombol logout dan tidak menghapus cookie, cache, atau data situs pada penutupan normal.
- Log hanya memuat tahap proses dan pesan kesalahan umum; jangan mencatat alamat email, isi formulir, URL yang memuat data sensitif, atau isi halaman secara utuh.
- Challenge keamanan dan CAPTCHA tidak dilewati. MFA memerlukan kode/persetujuan yang diberikan pengguna saat itu. Tombol persetujuan syarat Google/Gemini yang dikenali diklik otomatis sesuai instruksi pengguna.

### Keputusan penyimpanan

Profil Chrome permanen adalah tempat resmi untuk mempertahankan sesi login. Menu profil persisten Python yang sudah ada harus dipakai atau dikembangkan untuk alur ini. Pilih profil lama untuk memakai kembali sesinya, atau buat profil baru untuk akun/sesi baru. Email dan kata sandi diminta sebelum browser dibuka. Jika profil sudah aktif login dan halaman Gemini siap, jangan kirim kredensial tersebut; langsung gunakan sesi profil. Jika UI menunjukkan login diperlukan, isi formulir Google dengan kredensial dari run ini. Jangan membuat konteks sementara, menghapus profil, menghapus data situs, atau logout setelah selesai.

## 5. Bukan Tujuan

- Membuat atau mendaftarkan akun Google baru.
- Memulihkan akun Google yang tidak dapat diakses.
- Menyimpan kata sandi atau kode MFA di luar memori proses.
- Mengekspor cookie/token sesi Chrome ke Sheet, aplikasi, JSON, log, atau layanan lain.
- Menambahkan akun ke penyimpanan AMB Media atau mengekspor sesi ke fitur generate.
- Melewati CAPTCHA, pemeriksaan keamanan, pembatasan akun, atau syarat layanan.
- Mengirim prompt atau membuat konten Gemini. Target MVP hanya memastikan halaman chat siap.
- Menjalankan banyak akun secara batch.

## 6. Pengguna dan Cerita Pengguna

Pengguna utama adalah pengembang/tester Python di komputer Windows.

- Sebagai pengguna, saya ingin skrip otomatis mengisi kredensial run saat ini dan mengikuti tahapan login Google yang dikenal.
- Sebagai pengguna, saya ingin proses berhenti dengan jelas saat memerlukan MFA, lalu melanjutkan otomatis setelah kode/persetujuan saya tersedia.
- Sebagai pengguna, saya ingin tahu kapan Gemini benar-benar siap dipakai.
- Sebagai pengguna, saya ingin sesi login tetap ada di profil Chrome setelah browser ditutup.
- Sebagai pengguna, saya ingin memastikan sesi tersebut tidak dikirim ke Sheet, database aplikasi, atau log.

## 7. Alur Pengguna yang Diusulkan

1. Pengguna memilih profil Chrome lama atau membuat profil baru dari menu `Grab`.
2. Sebelum browser dibuka, skrip meminta email dan kata sandi; password tidak ditampilkan. Jika input kosong, proses batal dan Chrome tidak dijalankan.
3. Skrip memastikan profil tidak sedang dipakai proses Chrome lain, lalu membuka profil terpilih dengan direktori data yang sama.
4. Skrip membuka Gemini dan memeriksa UI. Jika chat siap dan Google tampak sudah login, tandai `session_active`, abaikan kredensial run ini, dan gunakan sesi yang tersimpan.
5. Jika UI menunjukkan login diperlukan, isi formulir Google dengan email/password dari awal run, tangani account chooser, konfirmasi identitas, layar error/retry, dan tunggu konfirmasi login.
6. Jika MFA berupa kode, minta kode sekali pakai secara tersembunyi lalu kirim tanpa menyimpannya. Jika berupa persetujuan ponsel, tunggu pengguna menyetujui dan periksa status halaman.
7. Setelah login terkonfirmasi, buka Gemini dan tangani onboarding rutin; tombol persetujuan syarat yang dikenali diklik otomatis tanpa prompt terminal.
8. Saat composer chat terlihat, tandai `ready` dan pertahankan sesi di profil Chrome.
9. Saat pengguna menutup browser atau kembali ke menu, tutup proses browser dengan normal tetapi pertahankan direktori profil dan sesi. Jangan logout atau menghapus data.

## 8. Status Proses

| Status | Arti |
|---|---|
| `profile_selected` | Profil tujuan dipilih atau dibuat. |
| `checking_profile` | Memastikan profil tidak sedang dipakai Chrome lain dan memeriksa UI Gemini. |
| `session_active` | UI menunjukkan Google sudah login dan Gemini dapat dipakai. |
| `login_required` | UI menunjukkan sesi tidak aktif atau login diperlukan. |
| `starting` | Membuka profil Chrome persisten. |
| `navigating_login` | Membuka Google Sign In. |
| `filling_credentials` | Mengisi email atau kata sandi run saat ini. |
| `waiting_for_auth` | Menunggu konfirmasi login Google atau mendeteksi halaman perantara. |
| `awaiting_2fa` | Menunggu kode MFA sementara atau persetujuan ponsel. |
| `navigating_gemini` | Membuka Gemini setelah login terkonfirmasi. |
| `accepting_terms` | Menangani onboarding dan mengklik tombol persetujuan yang dikenali secara otomatis. |
| `checking_gemini` | Memeriksa kesiapan composer chat. |
| `ready` | Antarmuka chat Gemini terlihat dan siap dipakai. |
| `timeout` | Batas waktu tercapai tanpa menemukan kondisi siap. |
| `error` | Browser atau navigasi gagal. |
| `cancelled` | Pengguna menghentikan proses. |
| `cleanup` | Menutup browser tanpa menghapus profil atau sesi tersimpan. |

## 9. Persyaratan Fungsional

| ID | Prioritas | Persyaratan |
|---|---|---|
| PY-AUTH-01 | P0 | Pengguna dapat memilih profil Chrome persisten atau membuat profil baru untuk alur Grab. |
| PY-AUTH-02 | P0 | Skrip menggunakan direktori data profil yang sama pada setiap run; jangan memakai konteks sementara. |
| PY-AUTH-03 | P0 | Skrip mendeteksi profil yang sedang dipakai dan mencegah dua proses Chrome menggunakan profil yang sama. |
| PY-AUTH-04 | P0 | Email/password diminta sebelum browser dibuka; jika sesi masih aktif, jangan kirim kredensial tersebut atau login ulang. |
| PY-AUTH-05 | P0 | Jika login diperlukan, skrip mengisi email dan kata sandi runtime pada domain resmi Google tanpa menyimpan kata sandi. |
| PY-AUTH-06 | P0 | Skrip mendeteksi form email/password, account chooser, konfirmasi identitas, error/retry, dan layar perantara dengan timeout. |
| PY-AUTH-07 | P0 | Skrip menangani MFA sesuai input/persetujuan pengguna tanpa menyimpan kode MFA. |
| PY-AUTH-08 | P0 | Skrip tidak membaca/mengekspor cookie atau token ke Sheet, aplikasi, JSON, log, atau layanan lain; Chrome tetap menyimpan sesi di profil terpilih. |
| PY-AUTH-09 | P0 | Menutup browser tidak melakukan logout, menghapus cookie/data situs, atau menghapus direktori profil. |
| PY-AUTH-10 | P0 | Skrip mendeteksi onboarding Gemini dan composer chat; status siap tidak hanya didasarkan pada URL. |
| PY-AUTH-11 | P0 | CAPTCHA tidak dilewati; tombol persetujuan Google/Gemini yang dikenali diklik otomatis tanpa prompt y/n di terminal. |
| PY-AUTH-12 | P1 | Skrip menampilkan status dan pesan aman tanpa kata sandi, kode MFA, token, atau isi halaman. |
| PY-AUTH-13 | P1 | Skrip memiliki timeout, retry terbatas, dan pembatalan tanpa logout atau penghapusan profil. |

## 10. Persyaratan Nonfungsional

- Privasi: sesi disimpan hanya oleh Chrome di profil terpilih; aplikasi tidak menyalin sesi ke penyimpanan lain. Kata sandi tidak disimpan.
- Keamanan: kredensial hanya diisi pada halaman resmi Google; verifikasi domain sebelum form diisi.
- Keandalan: pemeriksaan kondisi halaman mempunyai timeout dan pesan gagal yang jelas.
- Keterlacakan: log hanya berisi nama tahap, waktu, dan kategori kesalahan.
- Kemudahan uji: alur browser dapat diuji dengan profil khusus; uji penghapusan profil hanya dilakukan lewat tindakan eksplisit pengguna.
- Kesesuaian: tidak menerapkan teknik penyamaran atau pengelakan deteksi keamanan.

## 11. Penanganan Kondisi Khusus

- Login ditolak atau informasi akun salah: hentikan otomatisasi dengan pesan aman; jangan mencatat nilai formulir.
- Google meminta MFA kode: status menjadi `awaiting_2fa`, minta kode sementara, lalu kirim hanya setelah input pengguna.
- Google meminta persetujuan ponsel: tunggu tindakan pengguna dan verifikasi hasil sebelum lanjut.
- CAPTCHA atau browser ditolak: beri tahu pengguna bahwa otomasi tidak dapat melanjutkan; jangan mencoba bypass.
- Dialog syarat Google/Gemini muncul: klik otomatis tombol persetujuan yang dikenali tanpa meminta input terminal.
- Gemini tidak dapat diakses atau chat tidak ditemukan: tampilkan status gagal/timeout dan pilihan mencoba kembali.
- Pengguna menutup jendela browser: tandai proses selesai/dibatalkan dan pertahankan profil beserta sesi.
- Profil sedang dipakai: jangan membuka proses kedua; minta pengguna menutup jendela yang memakai profil tersebut terlebih dahulu.
- Sesi Google sudah kedaluwarsa: deteksi login yang diminta UI lalu jalankan login ulang dalam profil yang sama.
- Profil dihapus lewat menu khusus: tampilkan peringatan bahwa data situs dan sesi di dalam profil tersebut ikut terhapus, lalu minta konfirmasi eksplisit.

## 12. Kriteria Penerimaan

1. Profil baru dapat dibuat atau profil yang sudah ada dapat dipilih.
2. Email dan password diminta sebelum Chrome dibuka; input kosong membatalkan proses tanpa menjalankan Chrome.
3. Jika profil sudah memiliki sesi aktif, skrip mengenalinya melalui UI Gemini dan tidak mengirim kredensial yang dimasukkan.
4. Jika login diperlukan, email/password dari awal run diisi otomatis dan kata sandi tidak terlihat di log atau disimpan.
5. Login sukses dan onboarding mengarah ke composer chat Gemini.
6. Setelah browser ditutup lalu profil dibuka ulang, Google/Gemini tetap login selama Google belum mengakhiri sesi.
7. Tidak ada cookie/token yang disalin ke Sheet, database aplikasi, JSON, log, atau clipboard.
8. Proses tidak melakukan logout, menghapus data situs, atau membersihkan direktori profil saat selesai.
9. MFA tetap memakai tindakan pengguna; tombol persetujuan syarat yang dikenali diklik otomatis dan CAPTCHA tidak dilewati.

## 13. Risiko dan Asumsi

- Google dapat mengubah halaman login atau antarmuka Gemini sehingga deteksi elemen perlu diperbarui.
- Google dapat mengubah form atau menolak browser yang dikendalikan otomatis. Produk tidak akan menyamarkan otomasi atau melewati pemeriksaan keamanan.
- MFA membatasi otomasi tanpa pengawasan dan tetap memerlukan input/persetujuan pengguna; persetujuan syarat yang dikenali mengikuti instruksi otomatis pengguna.
- Kata sandi run-time dan kode MFA tidak disimpan; sesi login yang dikelola Chrome tetap bertahan dalam profil.
- Sesi dapat kedaluwarsa atau dicabut oleh Google; aplikasi mendeteksi login diperlukan dan mengautentikasi ulang pada profil sama.
- PRD Python Mode direct-generate yang lama masih mengasumsikan cookie dalam JSON. Asumsi tersebut tidak berlaku untuk fitur login ini dan perlu ditinjau sebelum kedua alur digabungkan.

## 14. Tahapan Implementasi yang Disarankan

1. Hubungkan menu Grab dengan profil Chrome persisten yang dipilih atau dibuat.
2. Cegah pembukaan ganda untuk satu direktori profil.
3. Deteksi sesi aktif dari UI sebelum meminta kredensial.
4. Implementasikan state machine login Google dan input kredensial runtime tanpa penyimpanan kata sandi.
5. Tambahkan penanganan account chooser, konfirmasi identitas, error/retry, MFA, persetujuan ponsel, dan onboarding Gemini.
6. Saat selesai, tutup browser tanpa logout atau menghapus profil.
7. Uji login baru, sesi aktif setelah membuka ulang, sesi kedaluwarsa, MFA, CAPTCHA, timeout, serta tidak adanya ekspor sesi ke luar Chrome.

## 15. Keputusan yang Harus Dipertahankan

Fitur Python harus otomatis mengikuti urutan login dan onboarding Playwright untuk akun Google yang sudah ada. Profil Chrome persisten adalah tempat sesi login disimpan dan digunakan kembali; tidak boleh ada logout atau pembersihan otomatis. Batas privasinya adalah tidak menyimpan kata sandi dan tidak mengekspor cookie/token ke Sheet, aplikasi, berkas, atau log. Fitur ini bukan pembuat akun Google baru.
