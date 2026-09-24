#!/usr/bin/env bash
# ==============================================================================
# Golden Lamian: Deployment & Provisioning Script for VPS Contabo (Ubuntu 24.04)
# Target:
# - User: goldenlamian
# - PWA: /home/goldenlamian/pwa
# - Admin: /home/goldenlamian/admin (Port 8502)
# - Domain: goldenlamian.dolanyu.com
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}  GOLDEN LAMIAN: AUTOMATED SERVER DEPLOYMENT (VPS CONTABO)       ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. Pastikan dieksekusi dengan hak akses root / sudo
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[ERROR] Script ini wajib dijalankan sebagai root atau dengan sudo!${NC}"
  exit 1
fi

# 2. Cek apakah user goldenlamian sudah ada
if id "goldenlamian" &>/dev/null; then
    echo -e "${GREEN}[OK] User 'goldenlamian' sudah tersedia.${NC}"
else
    echo -e "${YELLOW}[INFO] Membuat user Linux baru 'goldenlamian'...${NC}"
    adduser --disabled-password --gecos "Golden Lamian Attendance" goldenlamian
    echo -e "${GREEN}[OK] User 'goldenlamian' berhasil dibuat.${NC}"
fi

# 3. Buat struktur folder
echo -e "${YELLOW}[INFO] Menyiapkan struktur direktori di /home/goldenlamian...${NC}"
mkdir -p /home/goldenlamian/pwa
mkdir -p /home/goldenlamian/admin

# 4. Install dependensi sistem Ubuntu yang dibutuhkan
echo -e "${YELLOW}[INFO] Memeriksa paket Python 3 venv & Certbot Nginx...${NC}"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip certbot python3-certbot-nginx rsync

# 5. Setup Python Virtual Environment untuk HR Admin Portal
ADMIN_DIR="/home/goldenlamian/admin"
if [ -f "$ADMIN_DIR/requirements.txt" ]; then
    echo -e "${YELLOW}[INFO] Menyiapkan Python Virtual Environment untuk Admin Portal...${NC}"
    if [ ! -d "$ADMIN_DIR/venv" ]; then
        sudo -u goldenlamian python3 -m venv "$ADMIN_DIR/venv"
    fi
    echo -e "${YELLOW}[INFO] Menginstall dependensi requirements.txt...${NC}"
    sudo -u goldenlamian "$ADMIN_DIR/venv/bin/pip" install --upgrade pip -q
    sudo -u goldenlamian "$ADMIN_DIR/venv/bin/pip" install -r "$ADMIN_DIR/requirements.txt" -q
    echo -e "${GREEN}[OK] Dependensi Python Admin Portal berhasil terpasang.${NC}"
else
    echo -e "${YELLOW}[WARN] File $ADMIN_DIR/requirements.txt belum ditemukan. Pastikan Anda telah mengunggah file project admin sebelum mengaktifkan service.${NC}"
fi

# 6. Pastikan izin akses folder tepat
chown -R goldenlamian:goldenlamian /home/goldenlamian
chmod 755 /home/goldenlamian
chmod 755 /home/goldenlamian/pwa

# 7. Pasang Service Systemd
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_FILE="$SCRIPT_DIR/goldenlamian-admin.service"
if [ -f "$SERVICE_FILE" ]; then
    echo -e "${YELLOW}[INFO] Memasang systemd service: goldenlamian-admin.service...${NC}"
    cp "$SERVICE_FILE" /etc/systemd/system/goldenlamian-admin.service
    systemctl daemon-reload
    systemctl enable goldenlamian-admin.service
    if [ -f "$ADMIN_DIR/main.py" ]; then
        systemctl restart goldenlamian-admin.service
        echo -e "${GREEN}[OK] Service goldenlamian-admin berhasil dijalankan.${NC}"
    else
        echo -e "${YELLOW}[INFO] main.py belum ada, service belum di-start.${NC}"
    fi
fi

# 8. Pasang Konfigurasi Nginx
NGINX_CONF="$SCRIPT_DIR/nginx_goldenlamian.conf"
if [ -f "$NGINX_CONF" ]; then
    echo -e "${YELLOW}[INFO] Memasang konfigurasi Nginx: /etc/nginx/sites-available/goldenlamian.conf...${NC}"
    cp "$NGINX_CONF" /etc/nginx/sites-available/goldenlamian.conf
    ln -sf /etc/nginx/sites-available/goldenlamian.conf /etc/nginx/sites-enabled/goldenlamian.conf
    
    echo -e "${YELLOW}[INFO] Menguji sintaks konfigurasi Nginx (memastikan Dolanyu tidak terganggu)...${NC}"
    if nginx -t; then
        systemctl reload nginx
        echo -e "${GREEN}[OK] Nginx berhasil di-reload tanpa error!${NC}"
    else
        echo -e "${RED}[ERROR] Uji sintaks Nginx gagal! Membatalkan symlink agar Dolanyu tetap aman...${NC}"
        rm -f /etc/nginx/sites-enabled/goldenlamian.conf
        systemctl reload nginx
        exit 1
    fi
fi

# 9. Panduan SSL Let's Encrypt
echo -e "${BLUE}================================================================${NC}"
echo -e "${GREEN}  INSTALASI DASAR SERVER SELESAI DENGAN SUKSES!                 ${NC}"
echo -e "${BLUE}================================================================${NC}"
echo -e "Langkah Terakhir untuk Mengaktifkan HTTPS (SSL):"
echo -e "Pastikan DNS A Record ${YELLOW}goldenlamian${NC} sudah mengarah ke IP server ini, lalu jalankan:"
echo -e "${YELLOW}  sudo certbot --nginx -d goldenlamian.dolanyu.com${NC}"
echo -e ""
echo -e "URL Akses:"
echo -e "  - PWA Karyawan : ${GREEN}https://goldenlamian.dolanyu.com/${NC}"
echo -e "  - HR Admin     : ${GREEN}https://goldenlamian.dolanyu.com/admin/${NC}"
echo -e "${BLUE}================================================================${NC}"
