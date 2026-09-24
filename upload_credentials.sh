#!/usr/bin/env bash
# ==============================================================================
# Helper Script: Unggah Berkas Kredensial & Database ke VPS Contabo
# Jalankan di terminal Mac Anda setelah server selesai di-setup
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ADMIN_DIR="$SCRIPT_DIR/attendance_system_admin_v1"

echo "🔑 Golden Lamian - Helper Pengunggah Kredensial Pribadi ke Server"
echo "------------------------------------------------------------------"

if [ -z "$1" ]; then
    read -p "Masukkan IP Publik VPS Contabo Anda: " SERVER_IP
else
    SERVER_IP="$1"
fi

if [ -z "$SERVER_IP" ]; then
    echo "❌ Error: IP Server tidak boleh kosong."
    exit 1
fi

echo "🚀 Mengunggah .env dan file Google Cloud credentials ke root@$SERVER_IP..."

# 1. Unggah file .env
if [ -f "$ADMIN_DIR/.env" ]; then
    scp "$ADMIN_DIR/.env" "root@$SERVER_IP:/home/goldenlamian/admin/.env"
    echo "✅ Berkas .env berhasil diunggah."
else
    echo "⚠️ Berkas .env lokal tidak ditemukan di $ADMIN_DIR/.env"
fi

# 2. Unggah berkas JSON service account di folder data
scp "$ADMIN_DIR/data/"*.json "root@$SERVER_IP:/home/goldenlamian/admin/data/"
echo "✅ Berkas service account JSON berhasil diunggah."

# 3. Unggah database DuckDB (jika ada)
if [ -f "$ADMIN_DIR/data/hr_system.duckdb" ]; then
    scp "$ADMIN_DIR/data/hr_system.duckdb" "root@$SERVER_IP:/home/goldenlamian/admin/data/hr_system.duckdb"
    echo "✅ Database DuckDB lokal berhasil diunggah."
fi

# 4. Perbaiki hak kepemilikan di server via SSH
ssh "root@$SERVER_IP" "chown -R goldenlamian:goldenlamian /home/goldenlamian/admin && systemctl restart goldenlamian-admin.service"

echo "------------------------------------------------------------------"
echo "✨ SELURUH KREDENSIAL BERHASIL DIKONFIGURASI DI SERVER!"
echo "Service HR Admin Portal telah di-restart otomatis dengan kredensial baru."
echo "------------------------------------------------------------------"
