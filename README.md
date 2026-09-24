# 🍜 Golden Lamian - Serverless Smart Attendance System

Sistem pencatatan kehadiran karyawan terdistribusi berbasis **QR Code Dinamis (TOTP)**, **Pemindai Wajah Biometrik (Face Verification & Liveness Detection)**, serta **Geofencing GPS Radius Outlet**, terintegrasi dengan Google Sheets dan Portal HR Admin berbasis web.

---

## 📂 Struktur Repositori

```text
├── Github/                      # Frontend PWA Smartphone Karyawan (HTML5 / Vanilla JS / face-api.js)
│   ├── index.html               # Halaman utama PWA (Registrasi wajah & Scan QR)
│   ├── pwa_app.js               # Logika kamera, deteksi kedipan, GPS, dan pengiriman absensi
│   ├── sw.js                    # Service Worker (Offline caching & PWA engine)
│   └── models/                  # Bobot model AI biometrik wajah (TinyFaceDetector & FaceLandmark)
│
├── attendance_system_v1/        # Tampilan Monitor PC Outlet (Windows 7 Compatible)
│   ├── outlet_display.html      # Tampilan TV/Monitor kasir menampilkan QR Code dinamis berbasis waktu
│   └── qrcode.min.js            # Library generator QR code offline
│
├── attendance_system_admin_v1/  # Dashboard HR Admin Portal (Python / Streamlit / DuckDB)
│   ├── main.py                  # Entrypoint portal HR Admin
│   ├── ui/                      # Antarmuka dashboard, analitik, dan approval unbind
│   ├── modules/                 # Handler database DuckDB dan sinkronisasi Google Sheets
│   ├── sync_mp_database_cron.py # Skrip cron sinkronisasi berkala tab MP Database
│   └── requirements.txt         # Dependensi Python
│
├── Google/                      # Backend Serverless Google Cloud
│   └── google_apps_script.js    # API Google Apps Script (doPost/doGet) terhubung ke Google Sheets
│
├── server_deploy/               # Konfigurasi & Otomasi Deployment ke VPS Contabo
│   ├── nginx_goldenlamian.conf  # Virtual Host Nginx (PWA di '/' & Admin di '/admin/')
│   ├── goldenlamian-admin.service # Unit systemd Linux untuk HR Admin Portal
│   ├── setup_server.sh          # Skrip instalasi server otomatis Ubuntu 24.04
│   └── pack_for_upload.sh       # Skrip pembuat arsip bundle deployment
│
├── Doc/                         # Dokumentasi Arsitektur, Analisis & Walkthrough
│   ├── project_summary.md       # Ringkasan eksekutif dan teknis
│   ├── ARSIEKTEUR_DAN_ALUR_KERJA.md # Detail alur kerja dan skema data
│   └── walkthrough.md           # Panduan lengkap implementasi sistem
│
└── parameter.txt                # Catatan URL endpoint produksi
```

---

## 🚀 Panduan Ringkas Deployment ke VPS Contabo

### 1. Kebutuhan Server
* **OS:** Ubuntu 22.04 / 24.04 LTS
* **Web Server:** Nginx (Port 80 & 443 SSL)
* **Domain / Subdomain:** `goldenlamian.dolanyu.com`
* **User Linux:** `goldenlamian` (Terisolasi dari aplikasi lain di server)

### 2. Cara Cepat Instalasi
1. Generate paket deployment di komputer lokal:
   ```bash
   ./server_deploy/pack_for_upload.sh
   ```
2. Upload bundle ke VPS Contabo:
   ```bash
   scp goldenlamian_contabo_bundle.tar.gz root@<IP_CONTABO>:/root/
   ```
3. Login SSH ke VPS dan jalankan installer:
   ```bash
   ssh root@<IP_CONTABO>
   mkdir -p /root/goldenlamian_deploy
   tar -xzf /root/goldenlamian_contabo_bundle.tar.gz -C /root/goldenlamian_deploy
   cd /root/goldenlamian_deploy && sudo bash install.sh
   ```
4. Pasang SSL Let's Encrypt:
   ```bash
   sudo certbot --nginx -d goldenlamian.dolanyu.com
   ```

---

## 🌐 Endpoint Produksi

* **PWA Karyawan:** `https://goldenlamian.dolanyu.com/`
* **HR Admin Portal:** `https://goldenlamian.dolanyu.com/admin/`
* **Monitor Outlet:** Membuka file `attendance_system_v1/outlet_display.html` pada browser PC outlet.

---

## 🔒 Keamanan & Privasi
* Kunci API dan kredensial service account Google Cloud tidak disimpan di repositori publik.
* Salin `attendance_system_admin_v1/.env.example` menjadi `.env` pada server produksi dan masukkan ID spreadsheet serta path service account yang sah.
