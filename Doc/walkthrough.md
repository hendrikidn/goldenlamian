# 📘 Panduan Lengkap: Deployment Golden Lamian dari GitHub ke VPS Contabo
*(Panduan Praktis Langkah demi Langkah untuk Pemula)*

Panduan ini disusun khusus untuk Anda yang **belum memiliki pengalaman mengelola atau men-deploy server**. Seluruh langkah disederhanakan dengan perintah yang tinggal disalin (*copy-paste*) dan disertai penjelasan visual mengenai apa yang terjadi di layar komputer Anda.

---

## 🧭 Peta Alur Deployment (Mental Model)

```text
 [Komputer Mac Anda]                     [GitHub Repository]
 (Menyimpan project lokal)               (Tempat simpan kode online)
        │                                         │
        │                                         │ 1. git clone
        │                                         ▼
        │                              ┌──────────────────────┐
        │                              │     VPS CONTABO      │
        │                              │ (Ubuntu 24.04 LTS)   │
        │ 2. upload_credentials.sh     │                      │
        │ (Kirim .env & kunci Google)  │  • PWA Karyawan      │
        └─────────────────────────────►│  • HR Admin Portal   │
                                       │  • Nginx + SSL HTTPS │
                                       └──────────────────────┘
```

---

## 🛠️ TAHAP 1: Menyiapkan Domain (DNS A Record)

Sebelum server dapat diakses melalui internet menggunakan nama domain, kita harus mengarahkan alamat domain ke nomor IP server Contabo Anda.

1. Buka situs tempat Anda mengelola domain **`dolanyu.com`** (misalnya di Cloudflare, Namecheap, Domainesia, atau cPanel hosting Anda).
2. Cari menu bernama **DNS Management** atau **DNS Records**.
3. Klik tombol **Add Record** (Tambah Record), lalu masukkan data berikut:
   * **Type (Tipe):** `A`
   * **Name / Host:** `goldenlamian`
   * **IPv4 Address / Points to:** Masukkan **IP Publik VPS Contabo Anda** (contoh: `161.97.xxx.xxx`)
   * **TTL:** Biarkan `Auto` (atau `300`).
4. Klik **Save / Simpan**.

> [!NOTE]
> Setelah langkah ini selesai, alamat `goldenlamian.dolanyu.com` kini sudah tersambung dengan server Contabo Anda.

---

## 💻 TAHAP 2: Masuk ke Server VPS Contabo (SSH)

1. Di komputer Mac Anda, buka aplikasi **Terminal** (tekan tombol `Cmd + Spasi`, ketik `Terminal`, lalu tekan `Enter`).
2. Ketik perintah berikut lalu tekan `Enter` *(ganti `IP_CONTABO` dengan nomor IP server Anda)*:
   ```bash
   ssh root@IP_CONTABO
   ```
3. Jika muncul pertanyaan:
   `Are you sure you want to continue connecting (yes/no/[fingerprint])?`
   Ketik **`yes`** lalu tekan `Enter`.
4. Masukkan **Password root VPS Contabo** Anda:
   *(Catatan penting: Saat mengetik password di sistem Linux, huruf/bintang **sengaja tidak terlihat** di layar untuk keamanan. Tetap ketik password Anda dengan benar sampai selesai, lalu tekan `Enter`).*
5. Jika berhasil, Anda akan melihat tampilan selamat datang server Ubuntu: `root@vps:~#`.

---

## 📥 TAHAP 3: Ambil Kode dari GitHub & Jalankan Pemasangan Otomatis

Sekarang kita akan mengunduh kode dari GitHub dan membiarkan skrip otomatis mengatur user, Nginx, Python, dan service background.

Ketik perintah berikut satu per satu di terminal server:

### 1. Download kode dari GitHub:
```bash
git clone https://github.com/hendrikidn/goldenlamian.git
```
*(Tunggu 2–3 detik sampai muncul tulisan `done`)*.

### 2. Masuk ke folder proyek:
```bash
cd goldenlamian
```

### 3. Jalankan Skrip Pemasangan Otomatis:
```bash
sudo bash server_deploy/setup_server.sh
```

**Apa yang dilakukan oleh skrip ini secara otomatis?**
* ✅ Membuat user Linux terpisah bernama **`goldenlamian`** (menjaga aplikasi Dolanyu Anda tetap 100% aman).
* ✅ Menyiapkan folder `/home/goldenlamian/pwa` dan `/home/goldenlamian/admin`.
* ✅ Menginstall Python virtual environment dan modul yang dibutuhkan (Streamlit, DuckDB, Pandas, dll).
* ✅ Mengaktifkan service latar belakang **`goldenlamian-admin.service`** agar portal HR Admin menyala 24 jam nonstop.
* ✅ Memasang konfigurasi webserver **Nginx** tanpa mengganggu website Dolanyu yang sudah ada.

Tunggu hingga skrip selesai menampilkan pesan berwarna hijau:  
`✨ DEPLOYMENT GOLDEN LAMIAN KE SERVER BERHASIL!`

---

## 🔑 TAHAP 4: Mengunggah Kredensial Pribadi dari Komputer Mac Anda

> [!IMPORTANT]
> Karena file rahasia Google Service Account (`*.json`) dan `.env` **sengaja tidak disimpan di GitHub** (demi mencegah pencurian data oleh pihak luar), Anda perlu mengirimkannya sekali saja dari komputer Mac Anda ke server.

1. Di komputer Mac Anda, buka **Tab Terminal Baru** (tekan tombol `Cmd + T` pada aplikasi Terminal).  
   *(Pastikan tab baru ini berada di Mac Anda, bukan di dalam SSH server Contabo).*
2. Masuk ke folder proyek Golden Lamian di Mac Anda:
   ```bash
   cd /Users/henmei/Documents/projects/Golden_Lamian
   ```
3. Jalankan skrip pembantu pengunggah kredensial *(ganti `IP_CONTABO` dengan IP server Anda)*:
   ```bash
   ./upload_credentials.sh IP_CONTABO
   ```
4. Masukkan password root server Anda jika diminta.
5. Skrip akan otomatis mengunggah:
   * File konfigurasi `.env`
   * Kunci Service Account Google Cloud JSON
   * Database DuckDB lokal
   * Me-restart service HR Admin di server agar langsung mengenali kredensial tersebut.

---

## 🔒 TAHAP 5: Mengaktifkan Sertifikat Keamanan SSL HTTPS (Gratis)

PWA absensi (Kamera Wajah & GPS HP karyawan) **wajib menggunakan HTTPS** agar peramban ponsel mengizinkan akses sensor kamera.

1. Kembali ke **Tab Terminal pertama** (yang sedang terhubung ke SSH server Contabo Anda).
2. Jalankan perintah Certbot:
   ```bash
   sudo certbot --nginx -d goldenlamian.dolanyu.com
   ```
3. Jika Certbot pertama kali dijalankan, sistem akan meminta:
   * Masukkan alamat email Anda untuk notifikasi masa aktif sertifikat (misal: `admin@dolanyu.com`).
   * Ketik **`Y`** untuk menyetujui *Terms of Service*.
   * Ketik **`N`** atau **`Y`** jika ditanya mengenai newsletter EFF.
4. Tunggu beberapa detik hingga muncul tulisan:
   `Successfully received certificate.`  
   `Congratulations! You have successfully enabled https://goldenlamian.dolanyu.com`

---

## 📱 TAHAP 6: Menguji Coba Layanan

Buka peramban (browser) di laptop atau ponsel Anda:

1. **Aplikasi Karyawan (PWA):**
   * Buka: `https://goldenlamian.dolanyu.com/`
   * Layar scan wajah dan verifikasi NRP akan terbuka dengan koneksi aman (gembok hijau / HTTPS).
2. **Dashboard Portal HR Admin:**
   * Buka: `https://goldenlamian.dolanyu.com/admin/`
   * Halaman login HR Admin Portal (Streamlit) akan terbuka.
   * Masukkan password admin, dan klik tombol **`🔄 Refresh Data`** untuk memastikan data dari Google Sheets terhubung dengan sempurna.

---

## 🔄 TAHAP 7: Mengalihkan Layar Monitor di Seluruh Outlet

Kode pada file monitor outlet [`attendance_system_v1/outlet_display.html`](file:///Users/henmei/Documents/projects/Golden_Lamian/attendance_system_v1/outlet_display.html) **TIDAK PERLU DIUBAH SAMA SEKALI**.

Untuk mengubah alamat sasaran QR Code di semua cabang:
1. Buka spreadsheet Google Sheets Absensi Anda: [Spreadsheet ID: 1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA](https://docs.google.com/spreadsheets/d/1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA)
2. Buka tab **`Outlets`**.
3. Pada kolom **`pwa_url`**, masukkan nilai baru:
   ```text
   https://goldenlamian.dolanyu.com/
   ```
4. Selesai! Saat monitor di masing-masing cabang melakukan *auto-sync* berkala, QR Code yang ditampilkan akan otomatis mengarahkan kamera HP karyawan ke server Contabo baru Anda.

---

## 💡 Tips & Perintah Pemeliharaan Rutin

Jika di kemudian hari Anda melakukan update pada kode di GitHub dan ingin memperbarui server:
```bash
# Masuk ke server
ssh root@IP_CONTABO

# Masuk ke folder git dan tarik update terbaru
cd /root/goldenlamian
git pull origin main

# Jalankan ulang skrip installer (otomatis menyalin file baru dan restart service)
sudo bash server_deploy/setup_server.sh
```

Untuk melihat log aktivitas HR Admin Portal jika ada error:
```bash
sudo journalctl -u goldenlamian-admin -f
```
