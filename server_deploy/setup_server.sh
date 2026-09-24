#!/usr/bin/env bash
# ==============================================================================
# Golden Lamian: Deployment & Provisioning Script for VPS Contabo (Ubuntu 24.04)
# Supports:
# 1. Direct deployment from git clone:
#    git clone https://github.com/hendrikidn/goldenlamian.git
#    cd goldenlamian && sudo bash server_deploy/setup_server.sh
# 2. Automated user creation, Nginx, Python venv, and systemd service setup
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}  GOLDEN LAMIAN: AUTOMATED SERVER DEPLOYMENT (VPS CONTABO)       ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. Pastikan dieksekusi dengan hak akses root / sudo
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[ERROR] Script ini wajib dijalankan sebagai root atau dengan sudo!${NC}"
  echo -e "Silakan ketik: ${YELLOW}sudo bash $0${NC}"
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( dirname "$SCRIPT_DIR" )"

# 2. Cek apakah user goldenlamian sudah ada
if id "goldenlamian" &>/dev/null; then
    echo -e "${GREEN}[OK] User Linux 'goldenlamian' sudah tersedia.${NC}"
else
    echo -e "${YELLOW}[INFO] Membuat user Linux baru 'goldenlamian'...${NC}"
    adduser --disabled-password --gecos "Golden Lamian Attendance" goldenlamian
    echo -e "${GREEN}[OK] User 'goldenlamian' berhasil dibuat.${NC}"
fi

# 3. Buat direktori kerja di /home/goldenlamian
echo -e "${YELLOW}[INFO] Menyiapkan direktori di /home/goldenlamian...${NC}"
mkdir -p /home/goldenlamian/pwa
mkdir -p /home/goldenlamian/admin
mkdir -p /home/goldenlamian/admin/data

# 4. Jika script dijalankan dari repositori git, sinkronkan file proyek ke /home/goldenlamian
if [ -d "$REPO_ROOT/Github" ] && [ -d "$REPO_ROOT/attendance_system_admin_v1" ]; then
    echo -e "${YELLOW}[INFO] Repositori git terdeteksi! Menyalin file proyek ke /home/goldenlamian...${NC}"
    rsync -avq --exclude='.git*' "$REPO_ROOT/Github/" /home/goldenlamian/pwa/
    rsync -avq \
      --exclude='.git*' \
      --exclude='venv' \
      --exclude='.venv' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='*.exe' \
      --exclude='*.whl' \
      --exclude='resource' \
      "$REPO_ROOT/attendance_system_admin_v1/" /home/goldenlamian/admin/
    echo -e "${GREEN}[OK] Berkas PWA dan HR Admin Portal berhasil disinkronkan.${NC}"
fi

# 5. Salin .env.example jika .env belum ada
ADMIN_DIR="/home/goldenlamian/admin"
if [ ! -f "$ADMIN_DIR/.env" ] && [ -f "$ADMIN_DIR/.env.example" ]; then
    echo -e "${YELLOW}[INFO] Membuat file .env dari template .env.example...${NC}"
    cp "$ADMIN_DIR/.env.example" "$ADMIN_DIR/.env"
fi

# 6. Install dependensi sistem Ubuntu yang dibutuhkan
echo -e "${YELLOW}[INFO] Memeriksa paket pendukung (Python 3 venv, pip, certbot, rsync)...${NC}"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip certbot python3-certbot-nginx rsync

# 7. Setup Python Virtual Environment untuk HR Admin Portal
if [ -f "$ADMIN_DIR/requirements.txt" ]; then
    echo -e "${YELLOW}[INFO] Menyiapkan Python Virtual Environment (venv) di $ADMIN_DIR/venv...${NC}"
    if [ ! -d "$ADMIN_DIR/venv" ]; then
        sudo -u goldenlamian python3 -m venv "$ADMIN_DIR/venv"
    fi
    echo -e "${YELLOW}[INFO] Menginstall dependensi requirements.txt (Streamlit, DuckDB, Pandas, dll)...${NC}"
    sudo -u goldenlamian "$ADMIN_DIR/venv/bin/pip" install --upgrade pip -q
    sudo -u goldenlamian "$ADMIN_DIR/venv/bin/pip" install -r "$ADMIN_DIR/requirements.txt" -q
    echo -e "${GREEN}[OK] Seluruh dependensi Python berhasil terpasang.${NC}"
fi

# 8. Pastikan izin akses kepemilikan folder dipegang oleh user goldenlamian
chown -R goldenlamian:goldenlamian /home/goldenlamian
chmod 755 /home/goldenlamian
chmod 755 /home/goldenlamian/pwa

# 9. Pasang Systemd Service: goldenlamian-admin.service
SERVICE_FILE="$SCRIPT_DIR/goldenlamian-admin.service"
if [ -f "$SERVICE_FILE" ]; then
    echo -e "${YELLOW}[INFO] Memasang systemd service: goldenlamian-admin.service...${NC}"
    cp "$SERVICE_FILE" /etc/systemd/system/goldenlamian-admin.service
    systemctl daemon-reload
    systemctl enable goldenlamian-admin.service
    if [ -f "$ADMIN_DIR/main.py" ]; then
        systemctl restart goldenlamian-admin.service
        echo -e "${GREEN}[OK] Service goldenlamian-admin aktif dan berjalan.${NC}"
    fi
fi

# 10. Pasang Konfigurasi Nginx: /etc/nginx/sites-available/goldenlamian.conf
NGINX_CONF="$SCRIPT_DIR/nginx_goldenlamian.conf"
if [ -f "$NGINX_CONF" ]; then
    echo -e "${YELLOW}[INFO] Memasang konfigurasi Nginx untuk goldenlamian.dolanyu.com...${NC}"
    cp "$NGINX_CONF" /etc/nginx/sites-available/goldenlamian.conf
    ln -sf /etc/nginx/sites-available/goldenlamian.conf /etc/nginx/sites-enabled/goldenlamian.conf
    
    echo -e "${YELLOW}[INFO] Menguji sintaks Nginx (memastikan Dolanyu tidak terganggu)...${NC}"
    if nginx -t; then
        systemctl reload nginx
        echo -e "${GREEN}[OK] Uji sintaks Nginx valid dan berhasil di-reload!${NC}"
    else
        echo -e "${RED}[ERROR] Uji sintaks Nginx gagal! Membatalkan konfigurasi agar Dolanyu tetap aman...${NC}"
        rm -f /etc/nginx/sites-enabled/goldenlamian.conf
        systemctl reload nginx
        exit 1
    fi
fi

# 11. Informasi Status Akhir
echo -e "${BLUE}================================================================${NC}"
echo -e "${GREEN}  DEPLOYMENT GOLDEN LAMIAN KE SERVER BERHASIL!                  ${NC}"
echo -e "${BLUE}================================================================${NC}"
echo -e "Langkah Selanjutnya:"
echo -e "1. Aktifkan Sertifikat SSL (HTTPS) dengan perintah:"
echo -e "   ${YELLOW}sudo certbot --nginx -d goldenlamian.dolanyu.com${NC}"
echo -e ""
echo -e "2. Pastikan file Google Service Account JSON dan .env produksi diunggah ke:"
echo -e "   ${CYAN}/home/goldenlamian/admin/data/<nama-file-credentials>.json${NC}"
echo -e "   ${CYAN}/home/goldenlamian/admin/.env${NC}"
echo -e ""
echo -e "URL Layanan:"
echo -e "  - PWA Karyawan : ${GREEN}https://goldenlamian.dolanyu.com/${NC}"
echo -e "  - HR Admin     : ${GREEN}https://goldenlamian.dolanyu.com/admin/${NC}"
echo -e "${BLUE}================================================================${NC}"
