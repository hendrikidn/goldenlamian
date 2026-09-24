# 📘 Panduan Lengkap: Deployment Golden Lamian ke VPS Contabo
*(Menggunakan User Terpisah dari User `deploy` Eksisting)*

Panduan ini disesuaikan dengan kondisi server Anda saat ini:
* Anda sudah memiliki user **`deploy`** di server `46.250.228.75` untuk project mobile app lain.
* Login langsung sebagai `root` via password ditutup oleh server (*Permission denied publickey*), sehingga seluruh perintah setup kita jalankan menggunakan **`sudo` dari user `deploy`**.
* Kita akan membuat user mandiri bernama **`goldenlamian`** dengan direktori kerja terpisah di `/home/goldenlamian/` agar 100% terisolasi dari project `deploy`.

---

## 🧭 Peta Pemisahan Akun di Server (Zero Collision)

```text
                        VPS CONTABO (46.250.228.75)
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────────┐
│      PROJECT DOLANYU            │   │      PROJECT GOLDEN LAMIAN          │
│ • User Linux : deploy           │   │ • User Linux : goldenlamian         │
│ • Home Folder: /home/deploy     │   │ • Home Folder: /home/goldenlamian   │
│ • Web Folder : /var/www/dolanyu │   │ • PWA Folder : /home/goldenlamian/pwa
│ • Backend API: Port 4100        │   │ • HR Admin   : Port 8502 (internal) │
│ • Service    : dolanyu-api      │   │ • Service    : goldenlamian-admin   │
└─────────────────────────────────┘   └─────────────────────────────────────┘
```

---

## 🛠️ TAHAP 1: Menyiapkan DNS A Record
Di panel DNS domain `dolanyu.com`:
* **Type:** `A`
* **Host / Name:** `goldenlamian`
* **Points to / Value:** `46.250.228.75`
* **TTL:** `Auto` (atau `300`)

---

## 💻 TAHAP 2: Masuk ke Server menggunakan User `deploy`
Karena login `root` ditutup, masuklah menggunakan user `deploy` yang sudah Anda miliki:

```bash
ssh deploy@46.250.228.75
```
*(Anda sudah berhasil terhubung di terminal ini).*

---

## 👤 TAHAP 3: Buat User Baru `goldenlamian` & Direktori Terpisah

Jalankan perintah-perintah ini di jendela terminal server (di mana Anda sedang login sebagai `deploy`):

### 1. Buat user baru `goldenlamian` beserta folder home `/home/goldenlamian`:
```bash
sudo adduser --disabled-password --gecos "Golden Lamian Attendance" goldenlamian
```

### 2. Salin SSH Key dari user `deploy` ke user `goldenlamian`:
*(Langkah ini sangat penting agar dari laptop Mac Anda nantinya bisa langsung login atau mengirim file ke user `goldenlamian` tanpa ditolak).*
```bash
sudo mkdir -p /home/goldenlamian/.ssh
sudo cp ~/.ssh/authorized_keys /home/goldenlamian/.ssh/authorized_keys
sudo chown -R goldenlamian:goldenlamian /home/goldenlamian/.ssh
sudo chmod 700 /home/goldenlamian/.ssh
sudo chmod 600 /home/goldenlamian/.ssh/authorized_keys
```

### 3. Beri hak `sudo` ke user `goldenlamian`:
```bash
sudo usermod -aG sudo goldenlamian
```

---

## 📥 TAHAP 4: Ambil Kode dari GitHub & Jalankan Pemasangan Otomatis

Beralihlah ke user `goldenlamian` agar seluruh file project tersimpan di folder `/home/goldenlamian/`:

### 1. Masuk sebagai user `goldenlamian`:
```bash
sudo su - goldenlamian
```
*(Tampilan terminal Anda akan berubah menjadi `goldenlamian@...:~$`)*.

### 2. Download kode dari GitHub ke folder terpisah:
```bash
git clone https://github.com/hendrikidn/goldenlamian.git app_repo
```

### 3. Masuk ke folder repo dan jalankan installer:
```bash
cd app_repo
sudo bash server_deploy/setup_server.sh
```

> **Apa yang terjadi secara otomatis?**
> * Berkas PWA disalin ke `/home/goldenlamian/pwa/`.
> * Berkas HR Admin Portal disalin ke `/home/goldenlamian/admin/`.
> * Python Virtual Environment disiapkan dan dependensi (Streamlit, DuckDB, Pandas) diinstall.
> * Service latar belakang `goldenlamian-admin.service` otomatis aktif di port 8502.
> * Konfigurasi Nginx dipasang di `/etc/nginx/sites-available/goldenlamian.conf` dan diuji sintaksnya (memastikan Dolanyu tidak terganggu).

---

## 🔑 TAHAP 5: Unggah Kredensial Pribadi dari Laptop Mac Anda

Karena file kunci Google Service Account (`*.json`) dan `.env` tidak disimpan di GitHub demi keamanan, Anda cukup menjalankannya sekali dari Mac:

1. Di komputer Mac Anda, buka **Tab Terminal Baru** (tekan `Cmd + T`).  
   *(Pastikan berada di Mac Anda, bukan di dalam server).*
2. Masuk ke folder proyek Golden Lamian:
   ```bash
   cd /Users/henmei/Documents/projects/Golden_Lamian
   ```
3. Jalankan skrip pengunggah (menggunakan user `deploy` yang sudah memiliki akses SSH):
   ```bash
   ./upload_credentials.sh 46.250.228.75 deploy
   ```
4. Skrip akan otomatis mengunggah `.env`, Google Cloud Service Account JSON, DuckDB, dan me-restart service di server.

---

## 🔒 TAHAP 6: Aktifkan Sertifikat SSL HTTPS (Gratis)

Kembali ke jendela terminal server Contabo Anda, lalu jalankan:

```bash
sudo certbot --nginx -d goldenlamian.dolanyu.com
```
* Masukkan email jika diminta (misal: `admin@dolanyu.com`).
* Ketik **`Y`** untuk menyetujui *Terms of Service*.
* Selesai! HTTPS langsung aktif untuk `goldenlamian.dolanyu.com`.

---

## 📱 TAHAP 7: Uji Coba Layanan

1. **Aplikasi Karyawan (PWA):**  
   👉 `https://goldenlamian.dolanyu.com/` (Pastikan izin Kamera dan GPS aktif).
2. **Dashboard Portal HR Admin:**  
   👉 `https://goldenlamian.dolanyu.com/admin/` (Login dan coba tombol `🔄 Refresh Data`).

---

## 🔄 TAHAP 8: Alihkan QR Code di Semua Cabang

Kode pada file monitor outlet [`attendance_system_v1/outlet_display.html`](file:///Users/henmei/Documents/projects/Golden_Lamian/attendance_system_v1/outlet_display.html) **TIDAK PERLU DIUBAH SAMA SEKALI**.

Untuk mengarahkan QR Code ke server baru `https://goldenlamian.dolanyu.com/`, Anda memiliki 2 opsi mudah:

### OPSI 1: Tambahkan Kolom `pwa_url` di Tab `Outlets` (Rekomendasi - Otomatis ke Semua Cabang)
Secara bawaan Google Sheet awal hanya memiliki kolom *Outlet, Latitude, Longitude, Radius, Secret*. Sistem backend Google Apps Script sudah dirancang otomatis mengenali kolom `pwa_url` jika ditambahkan:
1. Buka spreadsheet Google Sheets Absensi Anda: [Spreadsheet ID: 1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA](https://docs.google.com/spreadsheets/d/1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA).
2. Buka tab **`Outlets`**.
3. Di sebelah kanan kolom `Secret` (misalnya kolom `F`), ketik nama header: **`pwa_url`**.
4. Isi sel di bawahnya untuk semua outlet dengan:
   ```text
   https://goldenlamian.dolanyu.com/
   ```
   *(Anda cukup copy-paste atau tarik ke bawah untuk seluruh baris outlet)*.
5. Selesai! Saat monitor PC outlet di cabang-cabang me-refresh atau sinkronisasi berkala, monitor akan otomatis mengambil URL baru dari kolom tersebut.

---

### OPSI 2: Ubah Langsung di Layar Monitor PC Outlet (Via Menu Pengaturan)
Jika Anda tidak ingin menambah kolom di Google Sheet, Anda bisa mengubahnya langsung di peramban PC outlet yang bersangkutan:
1. Di layar monitor outlet (`outlet_display.html`), klik tombol **⚙️ Konfigurasi / Edit** di pojok layar.
2. Masukkan password admin outlet.
3. Pada isian **URL PWA Karyawan (Hosting PWA)**, masukkan:
   ```text
   https://goldenlamian.dolanyu.com/
   ```
4. Klik tombol **Simpan & Jalankan**. Nilai ini akan tersimpan permanen di `localStorage` peramban PC cabang tersebut.
