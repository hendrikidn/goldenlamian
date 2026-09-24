# Project Summary: Serverless Smart Attendance System
**Versi Sistem:** 1.0  
**Tanggal:** 30 Juli 2026  
**Status:** Ready for Deployment / Operational  

---

## 📌 Ringkasan Eksekutif (Executive Summary)

**Serverless Smart Attendance System** adalah solusi pencatatan kehadiran karyawan terdistribusi berbasis QR Code Dinamis, Pemindai Wajah (Biometric Face Verification & Liveness Detection), serta Geofencing GPS. Sistem ini dirancang untuk beroperasi dengan **Zero Infrastructure Cost** (menggunakan Google Apps Script & GitHub Pages) dan kompatibel dengan perangkat komputer monitor outlet berbasis OS legasi (Windows 7).

Sistem ini memangkas biaya investasi perangkat keras fingerprint, menghilangkan potensi fraud absensi (titip absen), serta menyediakan data kehadiran secara *real-time* untuk manajemen HR dan operasional perusahaan.

---

## 🏢 SECTION 1: Perspective for Business & Executive Users

### 1. Business Challenges (Tantangan Bisnis)
* **Risiko Fraud / Titip Absen**: Pencatatan kehadiran konvensional atau manual berisiko tinggi manipulasi lokasi dan identitas karyawan.
* **Tinggi Biaya Pengadaan & Perawatan Hardware**: Pengadaan mesin fingerprint di tiap cabang/outlet membutuhkan biaya perangkat, instalasi jaringan, dan perawatan berkala.
* **Keterbatasan Perangkat Cabang**: Banyak outlet yang masih menggunakan PC dengan sistem operasi lama (Windows 7), sehingga tidak memungkinkan instalasi perangkat lunak modern yang berat.
* **Keterlambatan Rekapitulasi Data HR**: Proses rekap kehadiran dari puluhan outlet secara manual memakan waktu berhari-hari setiap akhir bulan.

### 2. Business Solution (Solusi Bisnis)
Sistem absensi berbasis kombinasi **Monitor Outlet** (menampilkan QR Code dinamis berbasis waktu) dan **Aplikasi Smartphone Karyawan PWA** (tanpa perlu unduh dari App Store/Play Store) yang terintegrasi langsung dengan **Dashboard Portal HR Admin**.

### 3. Key Business Values & Benefits (Nilai Tambah & Manfaat Utama)
* 💰 **Efisiensi Biaya Operasional (Zero Infra & Server Cost)**:
  * Memanfaatkan infrastruktur cloud serverless (Google Apps Script & GitHub Pages).
  * Tidak ada biaya sewa server bulanan atau pembelian lisensi database tahunan.
* 🛡️ **Anti-Kecurangan 100% (Zero Fraud Attendance)**:
  * **QR Code Dinamis**: QR di layar monitor PC outlet berubah setiap beberapa detik (berbasis TOTP), sehingga tidak bisa difoto atau dikirim via WhatsApp.
  * **Verifikasi Wajah & Kedipan Mata (Biometric Liveness)**: Mencegah manipulasi menggunakan foto/video wajah orang lain.
  * **Geofencing Radius GPS**: Karyawan wajib berada di lokasi fisik outlet.
  * **Device ID Binding**: Akun karyawan terkunci pada 1 smartphone. Pergantian HP wajib melalui persetujuan HR.
* 🖥️ **Kompatibilitas Tinggi pada Perangkat Lama**:
  * Aplikasi layar monitor outlet berjalan lancar di browser Google Chrome PC Windows 7 tanpa perlu upgrade hardware.
* ⚡ **Data Real-Time & Rekapitulasi Otomatis**:
  * HR Admin dapat memantau kehadiran detik demi detik secara terpusat melalui dashboard interaktif.
  * Ekspor laporan bulanan ke Excel/PDF dapat dilakukan hanya dengan 1 klik.

### 4. Target Operational Metrics (Metrik Keberhasilan Bisnis)
* **Eliminasi Titip Absen**: 100% terhindar dari praktik *buddy punching*.
* **Kecepatan Rekap Gaji (Payroll Processing Time)**: Memangkas waktu rekap data absensi dari **3-5 hari menjadi kurang dari 10 menit**.
* **Kecepatan Onboarding Karyawan**: Karyawan baru dapat langsung absen hanya dengan membuka link web di HP tanpa proses install aplikasi rumit.

---

## 💻 SECTION 2: Perspective for IT & Technical Teams

### 1. System Architecture & Tech Stack

Sistem dibangun menggunakan **Serverless & Distributed Edge Client Architecture** dengan pemisahan peran yang tegas antara layar monitor outlet, aplikasi PWA karyawan, backend serverless, dan portal admin HR.

```
+---------------------------+       +------------------------------------+
|  Outlet Display (PC)      |       |  Employee PWA (Mobile Smartphone)  |
|  - HTML5 / JS / Canvas    |       |  - PWA / HTML5 / JS                |
|  - TOTP Secret Key Generator      |  - face-api.js (Biometrics/Liveness)
|  - Real-time Polling      |       |  - Geolocation API (GPS)           |
+-------------+-------------+       +-----------------+------------------+
              |                                       |
              | (Get Today List)                      | (Submit Attendance)
              v                                       v
+------------------------------------------------------------------------+
|                   Google Apps Script Engine (GAS API)                  |
|                   - doPost(e) & doGet(e) Endpoints                     |
|                   - Geofence & TOTP Verification Rules                 |
+-----------------------------------+------------------------------------+
                                    |
                                    v
+------------------------------------------------------------------------+
|                     Google Sheets Container-Bound DB                   |
|                     - MP Database (Master Employee)                    |
|                     - Outlets (Location & TOTP Secrets)                |
|                     - attendance_records (Log Transaksi Absen)         |
|                     - Unbind_Requests (Request Reset Perangkat)        |
+-----------------------------------+------------------------------------+
                                    |
                                    v (gspread / REST API / DuckDB Engine)
+------------------------------------------------------------------------+
|                  HR Admin Portal (Python / Streamlit)                  |
|                  - Desktop Executable (HR Admin Portal.exe)            |
|                  - Master Data & Outlet Management                     |
|                  - Approval Workflow Unbind HP                         |
|                  - Analytics Dashboard (Plotly & Pandas)               |
+------------------------------------------------------------------------+
```

#### Tech Stack Summary:
* **Frontend Mobile**: HTML5, CSS3, JavaScript (ES6+), PWA Service Worker, `face-api.js` (TensorFlow.js core implementation).
* **Frontend Outlet Monitor**: HTML5 Native, Vanilla JS, `qrcode.min.js` (Offline-ready generation).
* **Hosting**: GitHub Pages (Static PWA Hosting with SSL/HTTPS).
* **Backend API & DB**: Google Apps Script (GAS) Web App & Google Sheets (Container-Bound Data Store).
* **HR Admin App**: Python 3.12, Streamlit 1.29.0, PyInstaller (Desktop Standalone Executable), DuckDB, Pandas, Plotly, `gspread`.

---

### 2. Deep-Dive Core Technical Components

#### A. Google Apps Script (Backend Serverless)
* **File Primary**: [google_apps_script.js](file:///c:/attendance_system_v1/google_apps_script.js)
* **Web App Deployment**:
  * Execute as: `Me` (Owner)
  * Access level: `Anyone` (API endpoint publik tanpa modal otentikasi Google OAuth pada client).
* **Fungsi Utama**:
  * `doPost(e)`: Memproses payload absensi masuk/keluar, validasi TOTP token, verifikasi koordinat GPS (Haversine formula), validasi device UUID, dan mencatat baris data ke tab `attendance_records`.
  * `doGet(e)`: Menyediakan data polling JSON untuk layar monitor outlet guna menampilkan karyawan yang sudah absen secara *live*.
  * `generateNewSecret()`: Utility internal untuk generate Base32 secret key per outlet.

#### B. Outlet Monitor Display (PC Outlet)
* **File Primary**: [outlet_display.html](file:///c:/attendance_system_v1/outlet_display.html), [qrcode.min.js](file:///c:/attendance_system_v1/qrcode.min.js)
* **Spesifikasi**:
  * Ringan (*lightweight*) & *offline-ready* untuk generasi QR Code.
  * Penyimpanan konfigurasi lokasi outlet & Secret Key secara lokal di browser (`localStorage`).
  * Generasi TOTP berbasis algoritma HMAC-SHA1 Base32 (di-refresh otomatis setiap 15 detik) untuk mencegah penipuan berbasis foto/tangkapan layar.
  * Kompatibilitas penuh dengan browser Google Chrome di **Windows 7** tanpa ketergantungan library modern yang membutuhkan WebGL/ES6 lanjutan.

#### C. Mobile Employee PWA
* **File Primary**: [index.html](file:///c:/attendance_system_v1/index.html), [pwa_app.js](file:///c:/attendance_system_v1/pwa_app.js), [sw.js](file:///c:/attendance_system_v1/sw.js)
* **Spesifikasi & Keamanan Client**:
  * **Biometric Liveness Detection**: Menggunakan `face-api.js` untuk mendeteksi kontur wajah dan mengukur rasio mata (EAR - Eye Aspect Ratio) untuk memverifikasi kedipan mata sebelum tombol kirim aktif.
  * **GPS Geofencing**: Mengambil posisi presisi Geolocation API di HP dan menghitung jarak ke titik koordinat Outlet. Jika melebihi `Radius` (misal 50 meter), transaksi ditolak.
  * **Device ID Fingerprinting**: Generasi UUID unik saat pendaftaran awal yang disimpan di `localStorage` HP karyawan.

#### D. HR Admin Portal Application
* **File Primary**: [main.py](file:///c:/attendance_system_v1/attendance_system_admin_v1/main.py) (Streamlit App) & Exe Package.
* **Fitur Utama**:
  * **Unbind Device Workflow**: Meninjau dan menyetujui/menolak pengajuan reset ID perangkat dari karyawan yang mengganti HP.
  * **Outlet & Employee Management**: CRUD master data karyawan dan lokasi cabang.
  * **Real-Time Analytics Dashboard**: Visualisasi tren kehadiran, ketepatan waktu, keterlambatan, dan jumlah karyawan aktif.

---

### 3. Security & Validation Pipeline

Setiap transaksi absensi yang dikirimkan oleh PWA Karyawan wajib melewati 5 lapisan verifikasi keamanan (*Multi-Layer Verification*):

| Layer | Nama Verifikasi | Tempat Eksekusi | Mekanisme Keamanan |
| :--- | :--- | :--- | :--- |
| **Layer 1** | **TOTP QR Code Verification** | Backend (GAS) | Token QR dicocokkan dengan Secret Key Outlet & Timestamp server (+/- margin toleransi waktu). Mencegah pemalsuan QR. |
| **Layer 2** | **Biometric Liveness Verification** | Client PWA (`face-api.js`) | Deteksi wajah asli + kedipan mata (blink check). Mencegah foto 2D / video spoofing. |
| **Layer 3** | **Geofencing GPS Radius** | Backend (GAS) | Formula Haversine menghitung koordinat karyawan vs koordinat outlet. Toleransi jarak disesuaikan (misal <= 50m). |
| **Layer 4** | **Device Binding Verification** | Backend (GAS) | Device UUID karyawan dicocokkan dengan data registrasi di `MP Database`. 1 NRP hanya bisa absen dari 1 HP terdaftar. |
| **Layer 5** | **Duplicate Attendance Guard** | Backend (GAS) | Mencegah double clock-in pada hari dan sesi yang sama. |

---

### 4. Maintenance & Operations Guide for IT

1. **Penambahan Outlet Baru**:
   * Jalankan fungsi `generateNewSecret()` di Google Apps Script editor.
   * Tambahkan baris outlet baru di tab `Outlets` (ID, Nama, Lat, Long, Radius, Secret Key).
   * Pada PC outlet baru, buka `outlet_display.html`, masukkan ID Outlet, Nama, Secret Key, dan URL Apps Script Web App.
2. **Onboarding Karyawan Baru**:
   * Daftarkan NRP dan Nama Karyawan di tab `MP Database`.
   * Karyawan membuka PWA di HP, melakukan registrasi perangkat & pendataan wajah pertama kali.
3. **Persetujuan Ganti HP (Device Unbind)**:
   * Karyawan mengajukan reset dari aplikasi PWA.
   * Tim HR / Admin IT membuka **HR Admin Portal** (`HR Admin Portal.exe`), masuk ke tab **Unbind Requests**, lalu memilih **Approve**.
4. **Deploy/Update Source Code**:
   * **Frontend PWA**: Commit & Push perubahan ke branch `main` pada repositori GitHub Pages. Tautan PWA akan ter-update otomatis dalam 1-2 menit.
   * **Backend GAS**: Update kode di Apps Script editor -> Klik **Deploy** -> **Manage Deployments** -> Edit ke **New Version** -> Klik **Deploy**.

---

## 📊 Comparison Summary Matrix

| Parameter | Sistem Manual / Fingerprint Konvensional | Serverless Smart Attendance System |
| :--- | :--- | :--- |
| **Biaya Hardware** | Tinggi (Beli mesin fingerprint per outlet) | **Rp 0** (Memanfaatkan PC outlet yang ada & HP Karyawan) |
| **Biaya Server / Cloud** | Ada (Sewa Cloud / Server Lokal) | **Rp 0** (Serverless GAS & GitHub Pages) |
| **Potensi Titip Absen** | Sedang (Bisa diakali jika tanpa supervisor) | **Hampir Nol** (TOTP QR + Liveness + GPS + Device Lock) |
| **Dukungan PC Lama** | Terbatas | **Penuh** (Optimized for Windows 7 & Chrome Browser) |
| **Akses Data HR** | Manual download dari mesin / kabel LAN | **Real-Time Synchronized** ke Google Sheets & Admin Portal |
| **Ketergantungan App Store** | Wajib download app native (PlayStore/AppStore) | **PWA Instant Access** (Cukup buka link web) |

---
*Dokumen ini disusun sebagai panduan standar overview proyek bagi pemangku kepentingan manajemen bisnis dan tim teknis IT.*
