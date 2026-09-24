# Walkthrough Implementasi: Absensi QR Code Serverless (GAS + PWA + Windows 7 Monitor)

Dokumen ini berisi panduan penyiapan, konfigurasi, dan pemasangan arsitektur absensi QR Code serverless dengan verifikasi wajah karyawan serta dashboard monitor PC berbasis HTML/JS statis yang **sepenuhnya kompatibel dengan Windows 7**.

Untuk menghindari pemblokiran kebijakan Google Workspace (*"This app is blocked"*), skrip dirancang agar berjalan sebagai **Single Spreadsheet (Container-bound)** tanpa mengakses file di luar dirinya sendiri.

---

## 1. Daftar File yang Tersedia di Repositori

1. **[google_apps_script.js](file:///c:/Golden_Lamian/Google/google_apps_script.js)**:
   * Backend serverless di Google Sheets. 
   * Menangani `doPost(e)` untuk pengiriman absensi dari HP (validasi TOTP & geofencing GPS).
   * Menangani `doGet(e)` untuk polling daftar kehadiran hari ini oleh layar PC outlet.
2. **[outlet_display.html](file:///c:/Golden_Lamian/attendance_system_v1/outlet_display.html)**:
   * Dashboard monitor PC outlet. **100% offline-ready untuk generate QR Code** dan ramah Windows 7.
   * Cukup dibuka langsung lewat peramban (Google Chrome) di PC outlet.
3. **[index.html](file:///c:/Golden_Lamian/Github/index.html)**:
   * Antarmuka aplikasi PWA ponsel karyawan (form registrasi wajah, pemindai QR, verifikasi liveness).
4. **[pwa_app.js](file:///c:/Golden_Lamian/Github/pwa_app.js)**:
   * Logika PWA karyawan: pelacakan wajah `face-api.js`, deteksi kedipan (liveness), GPS, dan antrean absensi offline.

---

## 2. Panduan Pengaturan Lengkap (Single Spreadsheet)

### Langkah 1: Siapkan Struktur Google Sheet Tunggal
Pastikan Anda memiliki **satu Google Sheet** yang berisi 3 buah tab berikut:
1. **Tab `MP Database`**:
   * Berisi data karyawan. Kolom minimal wajib ada: `NRP` dan `Nama Karyawan`. Header kolom dapat berada di baris ke-1 atau ke-2 (GAS akan mendeteksinya secara dinamis).
2. **Tab `Outlets`**:
   * Menampung data lokasi outlet dan kunci rahasianya:
     | Outlet ID | Nama Outlet | Latitude | Longitude | Radius | Secret |
     | :--- | :--- | :--- | :--- | :--- | :--- |
     | OUTLET_01 | Outlet Tebet | -6.22345 | 106.83456 | 50 | JBSWY3DPEHPK3PXP |
   * *Catatan*: **Secret** adalah kunci acak 16 karakter Base32 (karakter A-Z, 2-7) khusus per outlet untuk enkripsi QR Code.
3. **Tab `attendance_records`**:
   * Tempat pencatatan data masuk harian. Jika tab ini belum ada, skrip akan membuatnya otomatis beserta kolom headernya saat absensi pertama terkirim.

---

### Langkah 2: Deploy Google Apps Script (GAS)
1. Buka spreadsheet Google Sheet tersebut di browser Anda.
2. Klik menu **Extensions** > **Apps Script**.
3. Hapus seluruh kode bawaan di editor, lalu salin kode dari **[google_apps_script.js](file:///c:/attendance_system_v1/google_apps_script.js)**.
4. Klik **Save** (ikon disket).
5. Klik **Deploy** (kanan atas) > **New Deployment**.
6. Klik ikon gerigi (Select Type) dan pilih **Web App**.
7. Konfigurasikan parameter deployment:
   * *Description*: API Absensi QR PWA
   * *Execute as*: **Me (email-anda@gmail.com)**
   * *Who has access*: **Anyone** (wajib agar HP karyawan dan monitor PC bisa menembus API tanpa login Google).
8. Klik **Deploy**, klik **Authorize Access**, pilih akun Google Anda.
9. Saat muncul jendela peringatan keamanan (*Google hasn't verified this app*), klik tulisan **Advanced** di bagian bawah, lalu klik **Go to Untitled project (unsafe)** di paling bawah, lalu klik **Allow**.
10. **Salin URL Web App** yang dihasilkan (contoh: `https://script.google.com/macros/s/xxxx/exec`).

---

### Langkah 3: Cara Membuat (Generate) Secret Key Baru untuk Outlet Selanjutnya

Untuk mengaktifkan outlet baru di database, Anda memerlukan kunci rahasia (Secret Key Base32 16-karakter) yang unik agar QR Code dinamis dapat dienkripsi dengan aman. Anda bisa membuatnya langsung dari editor Google Apps Script:

1. Masuk ke halaman **Apps Script** yang sudah terpasang.
2. Pada bagian atas editor, pilih fungsi **`generateNewSecret`** pada menu drop-down di samping tombol "Run" (Jalankan).
3. Klik tombol **Run** (Jalankan) (tombol ikon segitiga/Play).
4. Menu **Execution log** akan muncul di bagian bawah layar editor. Anda akan melihat log seperti berikut:
   `🔑 SECRET KEY BASE32 BARU: KISD123DFGDFGDFG`
5. Salin kode rahasia 16-karakter tersebut (contoh: `KISD123DFGDFGDFG`) dan masukkan ke baris outlet baru Anda pada kolom **Secret** di tab `Outlets`.
6. Masukkan kunci yang sama ketika melakukan konfigurasi di monitor PC outlet baru tersebut.

---

### Langkah 4: Setup PWA Karyawan di GitHub Pages (Gratis & Mudah)

GitHub Pages adalah layanan hosting gratis dari GitHub untuk menampilkan file HTML, CSS, dan Javascript sebagai website aktif. Karyawan akan mengakses web ini dari HP mereka.

1. **Buat Akun & Repositori Baru**:
   * Buka [github.com](https://github.com) dan buat akun (jika belum punya).
   * Klik tombol **New** (atau ikon **+** di pojok kanan atas > **New repository**).
   * Konfigurasikan:
     * *Repository name*: Isi nama bebas tanpa spasi, misal: `absen-qr`.
     * *Visibility*: Pastikan Anda memilih **Public** (wajib agar GitHub Pages bisa diakses secara publik).
     * Biarkan opsi lainnya default, lalu klik **Create repository**.

2. **Unggah File PWA Anda**:
   * Pada halaman repositori baru Anda, cari dan klik tautan **"uploading an existing file"** di bagian atas.
   * Tarik (*drag & drop*) dua file berikut dari PC Anda ke area upload:
     * **[pwa_index.html](file:///c:/attendance_system_v1/pwa_index.html)** -> **PENTING**: Ganti nama file ini menjadi **`index.html`** sebelum atau setelah diunggah agar otomatis terdeteksi sebagai halaman utama oleh GitHub.
     * **[pwa_app.js](file:///c:/attendance_system_v1/pwa_app.js)** (Pastikan Anda sudah mengganti nilai variabel `GAS_URL` di dalam file ini dengan link Web App Google Apps Script Anda).
   * Tunggu hingga proses upload selesai, lalu klik tombol hijau **Commit changes** di bagian bawah.

3. **Aktifkan GitHub Pages**:
   * Pada repositori GitHub Anda, klik menu tab **Settings** (ikon gerigi di bilah menu atas).
   * Di menu navigasi sebelah kiri, cari bagian *Code and automation* dan klik **Pages**.
   * Di bawah menu *Build and deployment*:
     * *Source*: Pilih **Deploy from a branch**.
     * *Branch*: Klik dropdown menu bertuliskan `None`, ganti menjadi **`main`** (atau `master`), lalu biarkan folder sebelahnya tetap `/ (root)`.
     * Klik **Save** (Simpan).

4. **Kunjungi Link PWA Anda**:
   * Tunggu sekitar 1-2 menit sementara server GitHub mengaktifkan website Anda.
   * *Refresh* halaman *Settings > Pages* tersebut. Di bagian atas halaman, Anda akan melihat pesan berwarna hijau berisi tautan situs web Anda yang aktif, contoh:
     `Your site is live at https://username.github.io/absen-qr/`
   * Salin link tersebut dan bagikan ke karyawan untuk diakses di HP mereka.

---

### Langkah 5: Setup Dashboard Monitor PC Outlet (Windows 7)
1. Pastikan browser **Google Chrome** terpasang pada PC outlet Windows 7.
2. Salin folder **`attendance_system_v1`** (berisi `outlet_display.html`, `app_icon_v2.ico`, `qrcode.min.js`, dan `create_shortcut.bat`) ke harddisk PC outlet (misalnya di `C:\Absensi\` atau direktori mana pun).
3. Klik ganda (double click) file **`create_shortcut.bat`**. Shortcut **Outlet Attendance** dengan icon resmi akan langsung terbuat di Desktop Windows (bisa dijalankan oleh user standar / non-admin).
4. Buka shortcut **Outlet Attendance** di Desktop.
5. Pada pembukaan pertama, layar setup konfigurasi akan muncul di browser PC. Masukkan informasi berikut:
   * **Nama Outlet**: Pilih dari dropdown atau masukkan nama outlet (misal: `Outlet Tebet`).
   * **URL Web App**: URL Google Apps Script Anda.
   * Klik **Tarik dari Cloud** untuk mengisi *Secret Key* otomatis.
6. Klik **Simpan & Jalankan**. Dashboard akan menyimpan data di penyimpanan lokal browser (*localStorage*) dan mulai men-generate QR Code dinamis serta menampilkan daftar absen harian secara otomatis.
