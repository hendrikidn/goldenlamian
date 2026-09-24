# 🏛️ Arsitektur Sistem & Alur Kerja Terbarui (Updated System Architecture & Workflow)

**Serverless Smart Attendance System** v1.0  
*Sistem Absensi Cerdas Berbasis Dynamic TOTP QR, Biometric Face Verification, Geofencing GPS, dan Sync Offline-Ready.*

---

## 📌 1. Ikhtisar Arsitektur Sistem

Sistem ini dirancang menggunakan paradigma **Serverless & Edge-Distributed Client Architecture** tanpa memerlukan server backend berbayar mandiri (*Zero Infrastructure Cost*). Google Apps Script (GAS) difungsikan sebagai API Endpoint gratis (Web App) yang terhubung langsung secara *container-bound* dengan Google Sheets sebagai basis data utama.

```mermaid
graph TD
    subgraph PC_Outlet["💻 PC Outlet (Windows 7 / Chrome)"]
        OD["Outlet Display (HTML/JS)"]
        TOTP_GEN["TOTP Engine (HMAC-SHA1 Base32)<br>Refresh QR 15-30s (Offline-Ready)"]
        OD --> TOTP_GEN
    end

    subgraph HP_Employee["📱 HP Karyawan (PWA Mobile)"]
        PWA["PWA Mobile Client (HTML/JS)"]
        SCANNER["QR Scanner"]
        GPS["Geolocation API (GPS)"]
        FACE["face-api.js (Biometric & Liveness)"]
        OFFLINE_QUEUE["Offline Queue (LocalStorage/IndexedDB)"]
        
        PWA --> SCANNER
        PWA --> GPS
        PWA --> FACE
        PWA --> OFFLINE_QUEUE
    end

    subgraph Cloud_Backend["☁️ Google Cloud Infrastructure (Serverless)"]
        GAS["Google Apps Script (GAS Web App)"]
        VAL["Validation Engine:<br>1. TOTP Check<br>2. Haversine GPS<br>3. Device UUID<br>4. Face Liveness<br>5. Duplicate Guard"]
        GAS --> VAL
    end

    subgraph Data_Store["📊 Google Sheets Database"]
        GS["Google Sheets Container-Bound DB"]
        TAB1[("MP Database")]
        TAB2[("Outlets")]
        TAB3[("attendance_records")]
        TAB4[("Unbind_Requests")]
        GS --- TAB1
        GS --- TAB2
        GS --- TAB3
        GS --- TAB4
    end

    subgraph HR_Admin["🖥️ HR Admin Portal (Desktop App)"]
        ST["Streamlit Desktop Executable (.exe)"]
        DUCKDB["DuckDB Analytics Engine"]
        ST --- DUCKDB
    end

    %% Flow Connections
    PC_Outlet -- "Menampilkan Dynamic QR" --> HP_Employee
    HP_Employee -- "5a. HTTP POST Absen (Online)" --> GAS
    OFFLINE_QUEUE -- "7b. Auto-Sync Saat Internet Stabil" --> GAS
    GAS -- "Write Record / Fetch Data" --> GS
    HR_Admin -- "gspread / REST API (Read/Write)" --> GS
    GAS -- "8/9b. Response Notifikasi" --> HP_Employee
    GAS -- "doGet Polling Daftar Absen Hari Ini" --> PC_Outlet
```

---

## 🔄 2. Diagram Alur Kerja (Sequence Diagram)

Berikut adalah diagram sekuens alur kerja komprehensif yang mencakup skenario koneksi **Online** dan **Offline**:

```mermaid
sequenceDiagram
    autonumber
    actor PC as PC Outlet (Offline/Online)
    actor HP as HP Karyawan (PWA Mobile)
    participant GAS as Google Apps Script (Cloud)
    participant GS as Google Sheets (Database)

    Note over PC: 1. PC men-generate dynamic QR Code<br/>(Outlet ID + Timestamp + TOTP Token)<br/>setiap 15-30 detik secara offline
    PC->>HP: 2. Karyawan men-scan QR di monitor PC
    Note over HP: 3. Tangkap Koordinat GPS HP & Scan Timestamp
    Note over HP: 4. Kamera depan aktif otomatis:<br/>Verifikasi Wajah & Liveness Detection (Blink Check)

    alt Skenario Online (Koneksi HP Aktif)
        HP->>GAS: 5a. HTTP POST (NRP, GPS, Token, Timestamp, Face Status, Device UUID)
        Note over GAS: 6. Validasi Multi-Layer:<br/>- TOTP Secret Key<br/>- Jarak GPS (Haversine <= Radius)<br/>- Device UUID Binding<br/>- Status Verifikasi Wajah
        GAS->>GS: 7. Tulis Absen Sukses ke Sheet (attendance_records)
        GAS-->>HP: 8. Tampilkan Notifikasi "Absen Berhasil"
    else Skenario Offline (Sinyal HP Jelek / Blank Spot di Outlet)
        Note over HP: 5b. Simpan data scan, GPS, & Status Wajah<br/>ke Antrean Lokal (LocalStorage / IndexedDB)
        Note over HP: 6b. Begitu HP mendapat internet stabil<br/>(di luar outlet / sinyal pulih)
        HP->>GAS: 7b. Kirim otomatis antrean offline ke GAS
        Note over GAS: 8b. Validasi data antrean & catat absensi ke Sheet
        GAS-->>HP: 9b. Tampilkan Notifikasi "Absen Berhasil (Synced)"
    end

    Note over PC, GAS: Polling Otomatis (Background)
    PC->>GAS: doGet() Request Polling Kehadiran
    GAS-->>PC: Return JSON List Karyawan Absen Hari Ini (Update Layar Monitor)
```

---

## 🧩 3. Rincian Komponen Utama Sistem

### 💻 A. PC Outlet Display (Monitor Cabang)
* **File Utama:** [outlet_display.html](file:///c:/attendance_system_v1/outlet_display.html), [qrcode.min.js](file:///c:/attendance_system_v1/qrcode.min.js)
* **Fungsi & Keunggulan:**
  1. **100% Offline QR Generation:** Menggunakan pustaka ringan JavaScript untuk meng-generate QR Code dinamis berbasis waktu (TOTP HMAC-SHA1 Base32) tanpa memerlukan koneksi internet stabil.
  2. **Dukungan Perangkat Legasi:** Dirancang dan diuji sepenuhnya kompatibel dengan **Windows 7** dan peramban Google Chrome versi lama.
  3. **Live Attendance Board:** Melakukan polling `doGet()` berkala ke GAS untuk menampilkan daftar karyawan yang telah berhasil absen hari ini secara real-time di layar monitor.

### 📱 B. Mobile Employee PWA (Aplikasi HP Karyawan)
* **File Utama:** [index.html](file:///c:/attendance_system_v1/index.html), [pwa_app.js](file:///c:/attendance_system_v1/pwa_app.js), [sw.js](file:///c:/attendance_system_v1/sw.js)
* **Fungsi & Keunggulan:**
  1. **Zero App Store Installation:** Akses cepat via Progressive Web App (PWA) hosted di GitHub Pages (HTTPS).
  2. **Biometric Face Verification & Liveness Detection:** Menggunakan `face-api.js` (TensorFlow.js core) untuk mendeteksi landmark wajah dan Eye Aspect Ratio (EAR) untuk memverifikasi kedipan mata manusia asli sebelum absen diizinkan.
  3. **Geofencing GPS Integrasi:** Mengambil lokasi koordinat pengguna dari HTML5 Geolocation API.
  4. **Offline Storage & Auto-Sync Engine:** Menyimpan data absensi terenkripsi lokal di IndexedDB/LocalStorage jika tidak ada internet, dan secara otomatis mengirimkannya (*background retry*) ketika koneksi seluler kembali aktif.

### ☁️ C. Google Apps Script Backend (Serverless API)
* **File Utama:** [google_apps_script.js](file:///c:/attendance_system_v1/google_apps_script.js)
* **Fungsi & Keunggulan:**
  1. **Zero Server Maintenance Fee:** Berjalan pada infrastruktur serverless Google Apps Script dengan biaya operasional Rp 0.
  2. **Multi-Layer Validation Pipeline:** Memvalidasi TOTP Token, koordinat GPS (Haversine formula), Device UUID, dan pencegahan *double clock-in*.
  3. **Web App Endpoint:** `doPost(e)` untuk penulisan transaksi dan `doGet(e)` untuk pembacaan data polling.

### 📊 D. Google Sheets Database (Container-Bound Data Store)
* **Tab Utama:**
  1. **`MP Database`**: Data Master Karyawan (NRP, Nama, Device UUID Terdaftar).
  2. **`Outlets`**: Data Master Outlet (Outlet ID, Nama, Lat, Long, Radius M, Secret Key Base32).
  3. **`attendance_records`**: Log Transaksi Absensi (Timestamp, NRP, Nama, Outlet, Dist (m), Status Sync [Online/Offline], Device UUID).
  4. **`Unbind_Requests`**: Pengajuan Reset Device ID dari Karyawan.

### 🖥️ E. HR Admin Portal Application (Desktop Portal)
* **File Utama:** [main.py](file:///c:/attendance_system_v1/attendance_system_admin_v1/main.py) -> `HR Admin Portal.exe`
* **Fungsi & Keunggulan:**
  1. **Desktop Executable Standalone:** Aplikasi Windows berbasis Python + Streamlit yang dikompilasi dengan PyInstaller.
  2. **Workflow Unbind HP:** Persetujuan / penolakan reset ID perangkat ketika karyawan mengganti HP baru.
  3. **Analytics & Reporting:** Pengolahan data cepat menggunakan DuckDB & Pandas, serta visualisasi chart kehadiran interaktif dengan Plotly.

---

## 🛡️ 4. Pipa Keamanan 5 Lapisan (Multi-Layer Security Pipeline)

Setiap pengiriman transaksi absensi wajib lolos dari 5 pengujian keamanan berikut:

```
[ Input Payload Absensi ]
           │
           ▼
┌─────────────────────────┐
│ Layer 1: TOTP Check     │ ➔ Validasi Token QR vs Server Timestamp & Secret Key
└──────────┬──────────────┘
           │ (Valid)
           ▼
┌─────────────────────────┐
│ Layer 2: Face Liveness  │ ➔ Verifikasi deteksi kedipan mata & kontur wajah asli
└──────────┬──────────────┘
           │ (Valid)
           ▼
┌─────────────────────────┐
│ Layer 3: GPS Geofence   │ ➔ Hitung jarak Haversine (Koordinat HP vs Outlet <= Radius)
└──────────┬──────────────┘
           │ (Valid)
           ▼
┌─────────────────────────┐
│ Layer 4: Device Lock    │ ➔ Cocokkan Device UUID HP vs UUID terdaftar di MP Database
└──────────┬──────────────┘
           │ (Valid)
           ▼
┌─────────────────────────┐
│ Layer 5: Duplicate Guard│ ➔ Cek apakah NRP sudah melakukan clock-in di sesi yang sama
└──────────┬──────────────┘
           │ (Valid)
           ▼
[ Write to Google Sheets ]
```

---

## ⚡ 5. Perbandingan Skenario Online vs Offline Sync

| Parameter | Skenario Online (Koneksi HP Aktif) | Skenario Offline (Sinyal Jelek di Outlet) |
| :--- | :--- | :--- |
| **Generasi QR Code** | Ter-generate offline di PC (setiap 15-30s) | Ter-generate offline di PC (setiap 15-30s) |
| **Scan & Verifikasi HP** | Scan + GPS + Face Liveness aktif | Scan + GPS + Face Liveness aktif |
| **Penyimpanan Sementara** | Memori HP (Transient Payload) | **Local Storage / IndexedDB HP** |
| **Waktu Pengiriman** | Langsung (*Real-time*) via HTTP POST | Ditunda hingga HP berada di area sinyal stabil |
| **Validasi Backend** | Eksekusi langsung oleh GAS | Eksekusi saat antrean terkirim ke GAS |
| **Status Catatan Sheet** | Status: `ONLINE` | Status: `OFFLINE_SYNC` |

---

*Dokumen ini disusun sebagai acuan resmi Arsitektur & Alur Kerja Terbarui untuk Sistem Absensi Smart Serverless.*
