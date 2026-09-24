#!/usr/bin/env python3
"""
==============================================================================
Golden Lamian: Automated MP Database Synchronization Script
==============================================================================
Tujuan:
Membaca data Master Employee dari Google Sheet Working Paper HR
dan mereplikasi/menyinkronkan ke tab 'MP Database' di Google Sheet Absensi.

Dapat dijalankan secara manual atau dijadwalkan via cron di VPS Contabo:
Contoh crontab (setiap hari jam 06:00 WIB):
0 6 * * * /home/goldenlamian/admin/venv/bin/python /home/goldenlamian/admin/sync_mp_database_cron.py >> /home/goldenlamian/admin/data/cron_sync.log 2>&1
==============================================================================
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("MP_Database_Sync")

# Root directory project admin
ADMIN_ROOT = Path(__file__).resolve().parent
if (ADMIN_ROOT / "modules").exists():
    sys.path.insert(0, str(ADMIN_ROOT))
else:
    # Fallback jika dijalankan dari dalam server_deploy
    sys.path.insert(0, str(ADMIN_ROOT.parent / "admin"))

from modules.google_sheets import GoogleSheetsHandler

def main():
    logger.info("🚀 Memulai proses sinkronisasi MP Database...")
    
    # Load .env
    env_file = ADMIN_ROOT / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file)
    else:
        load_dotenv()

    # Ambil parameter konfigurasi
    credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "data/attendance-system-v1-504109-d4352cb12d5c.json")
    if not os.path.isabs(credentials_val):
        credentials_file = str(ADMIN_ROOT / credentials_val)
    else:
        credentials_file = credentials_val

    source_sheet_id = os.getenv("MP_DATABSE_SHEET_ID") or os.getenv("MP_DATABASE_SHEET_ID")
    target_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")

    if not source_sheet_id or not target_sheet_id:
        logger.error("❌ MP_DATABSE_SHEET_ID atau QR_ATTENDANCE_SHEET_ID belum disetting di .env!")
        sys.exit(1)

    if not Path(credentials_file).exists():
        logger.error(f"❌ File credentials tidak ditemukan: {credentials_file}")
        sys.exit(1)

    logger.info(f"📄 Source Master Sheet ID : {source_sheet_id}")
    logger.info(f"🎯 Target Absensi Sheet ID: {target_sheet_id}")
    logger.info(f"🔑 Credentials File       : {credentials_file}")

    try:
        sheets_handler = GoogleSheetsHandler(credentials_file=credentials_file, allow_offline=False)
        df_replicated = sheets_handler.replicate_mp_database(
            source_sheet_id=source_sheet_id,
            target_sheet_id=target_sheet_id
        )

        if not df_replicated.empty:
            logger.info(f"✅ SINKRONISASI SUKSES! {len(df_replicated)} data karyawan berhasil direplikasi ke tab 'MP Database'.")
        else:
            logger.warning("⚠️ Proses selesai tetapi tidak ada baris data yang direplikasi.")
            
    except Exception as e:
        logger.error(f"❌ Gagal melakukan sinkronisasi MP Database: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
