#!/usr/bin/env bash
# ==============================================================================
# Helper Script: Unggah Berkas Kredensial & Database ke VPS Contabo
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ADMIN_DIR="$SCRIPT_DIR/attendance_system_admin_v1"

echo "🔑 Golden Lamian - Helper Pengunggah Kredensial Pribadi ke Server"
echo "------------------------------------------------------------------"

SERVER_IP="${1:-46.250.228.75}"
# Prioritaskan user goldenlamian karena ia adalah pemilik direktori dan memiliki akses sudo NOPASSWD
SSH_USER="${2:-goldenlamian}"

echo -e "Target Server : $SSH_USER@$SERVER_IP"

# 1. Pastikan folder data di server ada
echo "🚀 Memeriksa direktori tujuan di server..."
if [ "$SSH_USER" = "goldenlamian" ]; then
    ssh "$SSH_USER@$SERVER_IP" "mkdir -p /home/goldenlamian/admin/data"
else
    # Jika menggunakan deploy, gunakan -t untuk alokasi terminal agar sudo dapat meminta password
    ssh -t "$SSH_USER@$SERVER_IP" "sudo mkdir -p /home/goldenlamian/admin/data && sudo chown -R $SSH_USER /home/goldenlamian/admin"
fi

# 2. Unggah file .env
if [ -f "$ADMIN_DIR/.env" ]; then
    echo "  -> Mengunggah .env..."
    scp "$ADMIN_DIR/.env" "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/.env"
    echo "✅ Berkas .env berhasil diunggah."
else
    echo "⚠️ Berkas .env lokal tidak ditemukan di $ADMIN_DIR/.env"
fi

# 3. Unggah berkas JSON service account di folder data
echo "  -> Mengunggah Service Account JSON..."
scp "$ADMIN_DIR/data/"*.json "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/data/"
echo "✅ Berkas service account JSON berhasil diunggah."

# 4. Unggah database DuckDB (jika ada)
if [ -f "$ADMIN_DIR/data/hr_system.duckdb" ]; then
    echo "  -> Mengunggah database DuckDB..."
    scp "$ADMIN_DIR/data/hr_system.duckdb" "$SSH_USER@$SERVER_IP:/home/goldenlamian/admin/data/hr_system.duckdb"
    echo "✅ Database DuckDB lokal berhasil diunggah."
fi

# 5. Pastikan hak kepemilikan tepat dan restart service
echo "  -> Me-restart service HR Admin Portal di server..."
if [ "$SSH_USER" = "goldenlamian" ]; then
    ssh "$SSH_USER@$SERVER_IP" "sudo systemctl restart goldenlamian-admin.service"
else
    ssh -t "$SSH_USER@$SERVER_IP" "sudo chown -R goldenlamian:goldenlamian /home/goldenlamian && sudo systemctl restart goldenlamian-admin.service"
fi

echo "------------------------------------------------------------------"
echo "✨ SELURUH KREDENSIAL BERHASIL DIKONFIGURASI DI SERVER!"
echo "Service HR Admin Portal telah aktif dengan kredensial baru."
echo "------------------------------------------------------------------"
