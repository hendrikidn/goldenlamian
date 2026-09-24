# Implementation Plan: Migrasi PWA Absensi & HR Admin Portal ke VPS Contabo

Rencana migrasi lengkap untuk:
1. **Frontend PWA Absensi Karyawan** (`Github/` -> `/home/goldenlamian/pwa`)
2. **HR Admin Portal** (`attendance_system_admin_v1/` -> `/home/goldenlamian/admin`)
3. **Zero Code Modification** pada `attendance_system_v1` (hanya setting parameter PWA ke `https://goldenlamian.dolanyu.com/`)
4. **Isolasi Penuh**: User khusus `goldenlamian`, virtual host Nginx terisolasi, dan port internal terpisah (port 8502) sehingga 100% aman tanpa mengganggu sistem **Dolanyu** (port 4100).

---

## 🏗️ Ringkasan Arsitektur Baru di VPS Contabo

```
               Internet (Karyawan & HR)
                         │
                         ▼
        ┌──────────────────────────────────┐
        │        Nginx Web Server          │
        │   goldenlamian.dolanyu.com       │
        │       (Port 80 & 443 SSL)        │
        └─────────────────┬────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │ Path: /                   │ Path: /admin/
            ▼                           ▼
┌─────────────────────────┐   ┌───────────────────────────────┐
│     PWA Absensi         │   │   HR Admin Portal (Streamlit) │
│ /home/goldenlamian/pwa  │   │  /home/goldenlamian/admin     │
│ (Static HTML/JS/Models) │   │  (Python venv @ 127.0.0.1:8502│
└───────────┬─────────────┘   └───────────────┬───────────────┘
            │                                 │
            │ (Direct Fetch via HTTPS)        │ (gspread API)
            ▼                                 ▼
┌─────────────────────────────────────────────────────────────┐
│             Google Sheets & Google Apps Script              │
│    ID: 1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA         │
│    - MP Database                                            │
│    - Outlets                                                │
│    - attendance_records                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📋 Detail Konfigurasi & Keputusan Teknis

| Item | Konfigurasi Target | Keterangan |
| :--- | :--- | :--- |
| **Domain** | `goldenlamian.dolanyu.com` | DNS A Record: Host `goldenlamian` -> IP Contabo |
| **User Linux** | `goldenlamian` | User terpisah di `/home/goldenlamian` |
| **Routing PWA** | `https://goldenlamian.dolanyu.com/` | Diakses karyawan dari HP untuk scan & liveness |
| **Routing Admin** | `https://goldenlamian.dolanyu.com/admin/` | Dashboard HR dengan login gate bawaan |
| **Admin Port** | `127.0.0.1:8502` | Internal localhost only (aman dari luar) |
| **Admin Service** | `goldenlamian-admin.service` | Systemd service auto-start saat reboot |
| **Database Admin** | DuckDB + Service Account JSON | Di `/home/goldenlamian/admin/data/` |
| **Aplikasi Eksisting**| Dolanyu (Port 4100 / `/var/www/dolanyu`) | **Sama sekali tidak tersentuh** |

---

## 🚀 Langkah demi Langkah Implementasi (Step-by-Step)

### Langkah 1: Persiapan DNS
Tambahkan **1 buah DNS A Record** di dashboard DNS domain Anda (`dolanyu.com`):
* **Type:** `A`
* **Host / Name:** `goldenlamian`
* **Points to / Value:** `<IP_PUBLIK_VPS_CONTABO>`
* **TTL:** Automatic / 300s

---

### Langkah 2: Buat User `goldenlamian` & Struktur Direktori

Jalankan perintah berikut di terminal VPS Contabo (sebagai root / sudo):

```bash
# 1. Buat user baru 'goldenlamian' dengan home folder
sudo adduser --disabled-password --gecos "" goldenlamian

# 2. Buat direktori kerja untuk PWA dan Admin Portal
sudo mkdir -p /home/goldenlamian/pwa
sudo mkdir -p /home/goldenlamian/admin

# 3. Beri izin hak akses Nginx untuk membaca folder publik
sudo chown -R goldenlamian:goldenlamian /home/goldenlamian
sudo chmod 755 /home/goldenlamian
sudo chmod 755 /home/goldenlamian/pwa
```

---

### Langkah 3: Transfer & Deployment File PWA

Upload file dari direktori `Github/` lokal ke `/home/goldenlamian/pwa/`:
* `Github/index.html`
* `Github/pwa_app.js`
* `Github/sw.js`
* `Github/models/` (Folder model AI biometrik)

**Contoh transfer via `rsync` dari komputer lokal:**
```bash
rsync -avz --progress ./Github/ root@IP_CONTABO:/home/goldenlamian/pwa/
sudo chown -R goldenlamian:goldenlamian /home/goldenlamian/pwa/
```

---

### Langkah 4: Transfer & Setup HR Admin Portal (`attendance_system_admin_v1`)

#### 4.1. Transfer File ke `/home/goldenlamian/admin/`
Upload seluruh isi `attendance_system_admin_v1/` kecuali file executable Windows (`.exe` dan `.bat`):
* `main.py`
* `ui/`
* `modules/`
* `utils/`
* `data/` (termasuk `attendance-system-v1-504109-d4352cb12d5c.json` dan `hr_system.duckdb`)
* `requirements.txt`
* `.streamlit/`
* `.env`

**Contoh transfer via `rsync` dari komputer lokal:**
```bash
rsync -avz --progress --exclude='*.exe' --exclude='*.bat' ./attendance_system_admin_v1/ root@IP_CONTABO:/home/goldenlamian/admin/
sudo chown -R goldenlamian:goldenlamian /home/goldenlamian/admin/
```

#### 4.2. Setup Python Virtual Environment (di Server Contabo)
Masuk sebagai user `goldenlamian` dan siapkan environment Python:

```bash
sudo -u goldenlamian -i
cd /home/goldenlamian/admin

# Buat virtual environment Python 3
python3 -m venv venv

# Aktivasi dan install dependensi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Tes jalankan Streamlit sekali untuk verifikasi
python -m streamlit run main.py --server.port 8502 --server.address 127.0.0.1 --server.baseUrlPath /admin --server.headless true &
sleep 3
curl -I http://127.0.0.1:8502/admin/
kill %1
exit
```

#### 4.3. Konfigurasi Systemd Service: `goldenlamian-admin.service`
Buat file service agar HR Admin Portal berjalan otomatis 24/7 di latar belakang:

```bash
sudo tee /etc/systemd/system/goldenlamian-admin.service > /dev/null << 'EOF'
[Unit]
Description=Golden Lamian HR Admin Portal (Streamlit)
After=network.target

[Service]
Type=simple
User=goldenlamian
Group=goldenlamian
WorkingDirectory=/home/goldenlamian/admin
ExecStart=/home/goldenlamian/admin/venv/bin/streamlit run main.py --server.port 8502 --server.address 127.0.0.1 --server.baseUrlPath /admin --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=5
EnvironmentFile=/home/goldenlamian/admin/.env

[Install]
WantedBy=multi-user.target
EOF

# Aktifkan dan jalankan service
sudo systemctl daemon-reload
sudo systemctl enable goldenlamian-admin.service
sudo systemctl start goldenlamian-admin.service
sudo systemctl status goldenlamian-admin.service
```

---

### Langkah 5: Konfigurasi Virtual Host Nginx

#### [NEW] `/etc/nginx/sites-available/goldenlamian.conf`
Buat file virtual host untuk melayani PWA di root `/` dan proxy Admin Portal di `/admin/`:

```nginx
server {
    listen 80;
    server_name goldenlamian.dolanyu.com;

    # 1. Frontend PWA Absensi
    root /home/goldenlamian/pwa;
    index index.html;

    # Header Keamanan PWA
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Kompresi Gzip
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/octet-stream;

    # Caching Agresif Model Biometrik AI (~7MB)
    location /models/ {
        expires 30d;
        add_header Cache-Control "public, no-transform, immutable";
        try_files $uri =404;
    }

    # Anti-caching Service Worker agar auto-update lancar
    location ~* (sw\.js|manifest\.json)$ {
        expires -1;
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0";
    }

    # 2. Reverse Proxy HR Admin Portal (Streamlit + WebSocket)
    location /admin {
        proxy_pass http://127.0.0.1:8502/admin;
        proxy_http_version 1.1;
        
        # Wajib untuk WebSocket Streamlit
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Fallback PWA
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Aktifkan konfigurasi Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/goldenlamian.conf /etc/nginx/sites-enabled/
sudo nginx -t   # Memastikan 100% valid dan konfigurasi Dolanyu tetap aman
sudo systemctl reload nginx
```

---

### Langkah 6: Pasang Sertifikat SSL Let's Encrypt (HTTPS)

```bash
sudo certbot --nginx -d goldenlamian.dolanyu.com --non-interactive --agree-tos -m admin@dolanyu.com
```

---

### Langkah 7: Pengaturan Parameter Outlet (Tanpa Ubah Code `attendance_system_v1`)

Kode program `attendance_system_v1/outlet_display.html` **tidak diubah sama sekali**.
Cukup lakukan update URL PWA baru:
* **Cara 1 (Otomatis se-Indonesia):** Buka Google Sheets `1ozd_CyxV7fVugEejI8gyCgquPKvjTie4LgxnPYEeLjA` -> Tab `Outlets` -> Kolom `pwa_url` isi dengan `https://goldenlamian.dolanyu.com/`. Seluruh monitor outlet akan otomatis mengambil URL baru.
* **Cara 2 (Manual di Monitor):** Klik tombol Konfigurasi di pojok outlet display -> Masukkan URL `https://goldenlamian.dolanyu.com/` pada kolom *URL PWA Karyawan* -> Simpan.

---

## 🧪 Verification Plan

### 1. Keamanan & Isolasi Aplikasi Lama (Dolanyu)
- [ ] Buka `https://dolanyu.com`, `https://app.dolanyu.com`, dan cek API di port 4100. Pastikan aplikasi Dolanyu tetap berjalan normal 100%.

### 2. Pengujian PWA Absensi Karyawan
- [ ] Buka `https://goldenlamian.dolanyu.com/` di smartphone (Chrome Android & Safari iOS).
- [ ] Pastikan izin Kamera dan GPS Geolocation berhasil diminta via HTTPS.
- [ ] Pastikan model AI wajah (`/models/`) terunduh cepat tanpa error 404.
- [ ] Lakukan deteksi kedipan (liveness) dan verifikasi NRP.

### 3. Pengujian HR Admin Portal
- [ ] Buka `https://goldenlamian.dolanyu.com/admin/` di browser desktop.
- [ ] Halaman login Streamlit muncul dengan normal.
- [ ] Coba login dan pastikan tombol `🔄 Refresh Data` berhasil membaca Google Sheets tab `MP Database`.
- [ ] Pastikan websocket Streamlit stabil (tidak ada pesan "Connecting..." yang berulang-ulang).

### 4. Pengujian End-to-End di Outlet
- [ ] Buka `attendance_system_v1/outlet_display.html` di PC outlet.
- [ ] Scan QR dinamis yang muncul di layar monitor outlet.
- [ ] Pastikan QR Code mengarahkan smartphone ke: `https://goldenlamian.dolanyu.com/?outlet=...`
- [ ] Lakukan absensi uji coba dan cek apakah data masuk ke tab `attendance_records` di Google Sheets dan terbaca di HR Admin Portal.
