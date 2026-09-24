#!/usr/bin/env bash
# ==============================================================================
# Helper Script: Unggah Berkas Kredensial & Database ke VPS Contabo
# Mendukung user non-root (misal: deploy atau goldenlamian)
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ADMIN_DIR="$SCRIPT_DIR/attendance_system_admin_v1"

echo "🔑 Golden Lamian - Helper Pengunggah Kredensial Pribadi ke Server"
echo "------------------------------------------------------------------"

SERVER_IP="${1:-46.250.228.75}"
SSH_USER="${2:-deploy}"

echo -e "Target Server : $SSH_USER@$SERVER_IP"

# 1. Pastikan folder data di server ada dan izin akses tepat
echo "🚀 Memeriksa direktori tujuan di server..."
ssh "$SSH_USER@$SERVER_IP" "sudo mkdir -p /home/goldenlamian/admin/data && sudo chown -R $SSH_USER /home/goldenlamian/admin"

# 2. Unggah file .env
if [ -f "$ADMIN_DIR/.env" ]; then
    scp "$ADMIN_DIR/.env" "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/.env"
    echo "✅ Berkas .env berhasil diunggah."
else
    echo "⚠️ Berkas .env lokal tidak ditemukan di $ADMIN_DIR/.env"
fi

# 3. Unggah berkas JSON service account di folder data
scp "$ADMIN_DIR/data/"*.json "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/data/"
echo "✅ Berkas service account JSON berhasil diunggah."

# 4. Unggah database DuckDB (jika ada)
if [ -f "$ADMIN_DIR/data/hr_system.duckdb" ]; then
    scp "$ADMIN_DIR/data/hr_system.duckdb" "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/data/hr_system.duckdb"
    echo "✅ Database DuckDB lokal berhasil diunggah."
fi

# 5. Kembalikan hak kepemilikan ke user goldenlamian dan restart service
ssh "$SSH_USER@$SERVER_IP" "sudo chown -R goldenlamian:goldenlamian /home/goldenlamian && sudo systemctl restart goldenlamian-admin.service"

echo "------------------------------------------------------------------"
echo "✨ SELURUH KREDENSIAL BERHASIL DIKONFIGURASI DI SERVER!"
echo "Service HR Admin Portal telah di-restart otomatis dengan kredensial baru."
echo "------------------------------------------------------------------"
