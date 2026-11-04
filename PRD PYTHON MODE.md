# PRD — Python Mode Direct Generate

Status: Draft awal untuk testing direct generate dari file JSON
Tanggal: 2026-11-03
Folder: `pythone mode`
Tujuan: Mencoba generate video Gemini langsung dari Python tanpa server AMB Media, memakai akun dari JSON lokal.

> Status: Sudah tidak berlaku. `generate_direct.py` dan `akun.json` telah dihapus atas permintaan pengguna. Dokumen ini hanya dipertahankan sebagai catatan historis; alur aktif dijelaskan di `PRD PYTHON GRAB LOGIN GEMINI PROFILE PERSISTEN.md`.

## 1. Ringkasan

Python Mode adalah mode testing sederhana untuk menjalankan generate video Gemini langsung dari Python. Mode ini tidak memakai UI AMB Media, tidak memakai server aplikasi, dan tidak memakai login multi-user. Satu akun dimasukkan ke file JSON lokal, lalu Python membaca akun tersebut, menyiapkan request generate, dan memantau status task sampai selesai atau gagal.

Tujuan utamanya adalah menilai apakah pendekatan direct Python lebih stabil daripada CLI yang lewat server.

## 2. Masalah yang diselesaikan

Saat ini ada dua cara testing yang sedang dipikirkan:

1. CLI lewat server AMB Media
2. Python direct tanpa server

Kedua cara itu perlu dibandingkan dari sisi:
- stabilitas request
- keandalan task polling
- lama waktu proses
- tingkat error
- kebutuhan cookie/session
- kemudahan debug

Mode Python direct ini dibuat supaya tester bisa menjalankan generate secara lebih cepat dan lebih bersih, tanpa harus melalui flow server yang bisa menambah latency.

## 3. Tujuan produk

1. Menggunakan satu akun Gemini dari file JSON lokal
2. Mengirim request generate langsung lewat Python
3. Tidak membutuhkan login UI atau server AMB Media
4. Menampilkan hasil status request dan task ID dengan jelas
5. Menganalisa apakah direct Python lebih stabil dibanding CLI server
6. Menyediakan baseline testing yang cepat dan mudah diulang

## 4. Bukan tujuan

- Multi akun di phase awal
- Login user AMB Media
- UI dashboard
- Batch processing
- Integrasi ke Google Sheets
- Fitur produksi untuk end user
- Menyimpan credential di log atau output terminal

## 5. Target pengguna

Pengguna utama adalah tester/developer yang ingin:
- mengecek generate Gemini dari Python
- memakai akun yang sudah disiapkan dari JSON
- membandingkan kestabilan direct Python vs CLI server

## 6. User story

- Sebagai tester, saya ingin memakai satu akun dari file JSON agar proses cepat
- Sebagai tester, saya ingin generate langsung lewat Python tanpa server
- Sebagai tester, saya ingin melihat task status, error, dan duration dengan jelas
- Sebagai tester, saya ingin membandingkan hasil direct Python dan CLI server

## 7. Scope MVP

### Fitur wajib
- Baca file JSON akun lokal
- Gunakan satu akun saja
- Ambil cookies/header dari JSON
- Kirim request generate langsung ke endpoint Gemini
- Pantau status task sampai terminal
- Tampilkan hasil sukses/gagal
- Tulis log ringkas ke console atau file log

### Fitur opsional nanti
- Multiple akun
- Retry otomatis
- Simpan report hasil compare
- Batch generate
- CLI mode compare

## 8. Struktur JSON akun

Karena saat ini hanya satu akun, struktur JSON harus sederhana dan langsung dipakai.

Contoh format yang disarankan:

```json
{
  "name": "akun utama",
  "email": "user@gmail.com",
  "cookies": {
    "SID": "...",
    "HSID": "...",
    "SSID": "...",
    "APISID": "...",
    "SAPISID": "...",
    "__Secure-1PSID": "...",
    "__Secure-3PSID": "..."
  },
  "headers": {
    "user-agent": "Mozilla/5.0 ...",
    "x-goog-api-key": "...",
    "x-goog-authuser": "0",
    "origin": "https://gemini.google.com",
    "referer": "https://gemini.google.com/"
  },
  "prompt_default": "A cinematic drone shot of a futuristic city at sunset"
}
```

Jika JSON yang ada berbentuk curl mentah, maka script Python akan melakukan parsing terhadap cookies dan headers dari string curl tersebut saat awal run. Output final tetap harus disimpan dalam format terstruktur untuk keperluan testing.

## 9. Alur kerja

1. Python membaca file JSON akun
2. Python mengekstrak cookies dan headers yang dibutuhkan
3. Python menyiapkan prompt teks sesuai input user
4. Python mengirim request generate ke endpoint Gemini langsung
5. Python menerima response task atau operation ID
6. Python melakukan polling status sampai hasil final
7. Python menampilkan status request, elapsed time, dan hasil akhir
8. Python menulis ringkasan testing ke log

## 10. Persyaratan fungsional

| ID | Prioritas | Persyaratan |
|---|---|---|
| PY-01 | P0 | Python membaca file JSON akun lokal dari folder project |
| PY-02 | P0 | Mode ini hanya memakai satu akun aktif |
| PY-03 | P0 | Script generate langsung lewat Python tanpa server AMB Media |
| PY-04 | P0 | Cookie/header dari JSON dipakai saat request dibuat |
| PY-05 | P0 | Script memantau task status sampai final |
| PY-06 | P0 | Output menampilkan status, error, dan durasi |
| PY-07 | P1 | Prompt default bisa diubah lewat argumen atau file |
| PY-08 | P1 | Log hasil bisa disimpan ke file untuk comparison |
| PY-09 | P1 | Script bisa dieksekusi berulang untuk test multiple run |

## 11. Persyaratan non-fungsional

- Keamanan: cookie/token tidak boleh tampil di console
- Stabilitas: script harus mudah diulang untuk testing
- Portability: bisa dijalankan di terminal lokal Windows
- Simplicity: tidak perlu arsitektur kompleks
- Debug-friendly: error harus jelas dan ringkas

## 12. Penanganan error

- File JSON tidak ditemukan: tampilkan pesan error jelas
- JSON rusak: tampilkan pesan parse error
- Cookie/header tidak lengkap: stop proses dan beri alasan
- Endpoint gagal: tampilkan status HTTP dan pesan error dari response
- Task timeout: tampilkan status timeout dan task ID
- Polling gagal: beri status ambiguous dan stop tanpa retry sembarangan
- Session expired: tampilkan bahwa akun tidak valid untuk direct mode

## 13. Kriteria penerimaan

1. Script dapat membaca file JSON yang berisi akun tunggal
2. Script bisa generate langsung tanpa server AMB Media
3. Script memakai cookie/header dari JSON tanpa menampilkannya ke output
4. Script mengikuti task status sampai final atau gagal
5. Output menampilkan status yang mudah dibaca untuk testing
6. Script bisa dipakai berulang untuk compare hasil stabilitas
7. Tidak ada kebutuhan login UI atau server untuk mode ini

## 14. Risiko dan asumsi

- JSON yang dipakai kemungkinan berisi cookie sensitif, jadi harus diperlakukan sebagai secret
- Session dan cookie bisa kadaluarsa, jadi script perlu menangani expired session dengan jelas
- Direct mode tidak sama dengan production flow dan harus dianggap sebagai testing-only mode
- Jika endpoint memerlukan header tertentu yang tidak ada, script akan gagal dengan pesan yang jelas

## 15. Rencana implementasi awal

Implementasi awal akan mencakup:

- file JSON akun
- file Python utama `generate_direct.py`
- parsers cookies/header dari JSON atau curl string
- function untuk request generate
- function polling status
- function logging ringkas
- output terminal yang sederhana dan mudah diulang

## 16. Kesimpulan

Python Mode direct adalah mode testing paling sederhana untuk mengecek apakah generate dari Python tanpa server lebih stabil dibanding CLI yang bergantung pada link server. Karena target penggunaan saat ini hanya satu akun, fokus utama adalah kecepatan, kedalaman log, dan kemudahan debugging, bukan kompleksitas multi-user.

Mode ini akan menjadi baseline sebelum kita menambah fitur lanjutan seperti multi akun, compare report, atau mode CLI yang lebih formal.
