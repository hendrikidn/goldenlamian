#!/usr/bin/env bash
# ==============================================================================
# Golden Lamian: Script Pembuat Bundle Siap Upload ke VPS Contabo
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
BUNDLE_NAME="goldenlamian_contabo_bundle.tar.gz"
TEMP_DIR="$PROJECT_ROOT/.deploy_temp"

echo "📦 Menyiapkan arsip paket deployment..."

# Bersihkan folder sementara
rm -rf "$TEMP_DIR" "$PROJECT_ROOT/$BUNDLE_NAME"
mkdir -p "$TEMP_DIR/pwa"
mkdir -p "$TEMP_DIR/admin"
mkdir -p "$TEMP_DIR/deploy"

# 1. Salin PWA (Folder Github/)
echo "  -> Menyalin file PWA..."
rsync -avq --exclude='.git*' "$PROJECT_ROOT/Github/" "$TEMP_DIR/pwa/"

# 2. Salin Admin Portal (Kecuali binary .exe / .bat Windows, wheel Windows, dan virtualenv lokal)
echo "  -> Menyalin file HR Admin Portal..."
rsync -avq \
  --exclude='*.exe' \
  --exclude='*.bat' \
  --exclude='*.lnk' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='venv' \
  --exclude='.venv' \
  --exclude='resource' \
  "$PROJECT_ROOT/attendance_system_admin_v1/" "$TEMP_DIR/admin/"

# 3. Salin Skrip Deployment Server
echo "  -> Menyalin skrip instalasi server..."
cp "$SCRIPT_DIR/nginx_goldenlamian.conf" "$TEMP_DIR/deploy/"
cp "$SCRIPT_DIR/goldenlamian-admin.service" "$TEMP_DIR/deploy/"
cp "$SCRIPT_DIR/setup_server.sh" "$TEMP_DIR/deploy/"
chmod +x "$TEMP_DIR/deploy/setup_server.sh"

# 4. Tambahkan Skrip Runner di root paket
cat << 'EOF' > "$TEMP_DIR/install.sh"
#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Error: Jalankan script ini sebagai root (sudo bash install.sh)"
  exit 1
fi

echo "🚀 Memulai instalasi Golden Lamian ke server..."

# Pastikan user goldenlamian ada
if ! id "goldenlamian" &>/dev/null; then
    adduser --disabled-password --gecos "Golden Lamian Attendance" goldenlamian
fi

# Pindahkan file ke /home/goldenlamian
mkdir -p /home/goldenlamian/pwa
mkdir -p /home/goldenlamian/admin

cp -r pwa/* /home/goldenlamian/pwa/
cp -r admin/* /home/goldenlamian/admin/

chown -R goldenlamian:goldenlamian /home/goldenlamian
chmod 755 /home/goldenlamian
chmod 755 /home/goldenlamian/pwa

# Eksekusi setup server
cd deploy
bash setup_server.sh

echo "✨ Ekstraksi dan instalasi selesai!"
EOF
chmod +x "$TEMP_DIR/install.sh"

# 5. Kompres menjadi tar.gz
echo "  -> Mengompres ke $BUNDLE_NAME..."
tar -czf "$PROJECT_ROOT/$BUNDLE_NAME" -C "$TEMP_DIR" .
rm -rf "$TEMP_DIR"

echo "✅ SELESAI! Bundle berhasil dibuat: $PROJECT_ROOT/$BUNDLE_NAME"
echo ""
echo "Cara Upload dan Pasang di VPS Contabo Anda:"
echo "1. Upload bundle ke server:"
echo "   scp $PROJECT_ROOT/$BUNDLE_NAME root@IP_CONTABO:/root/"
echo ""
echo "2. SSH ke server Contabo dan jalankan:"
echo "   ssh root@IP_CONTABO"
echo "   mkdir -p /root/goldenlamian_deploy && tar -xzf /root/$BUNDLE_NAME -C /root/goldenlamian_deploy"
echo "   cd /root/goldenlamian_deploy && sudo bash install.sh"
echo ""
