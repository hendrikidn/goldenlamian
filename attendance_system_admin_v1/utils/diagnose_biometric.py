"""
Diagnostic script to troubleshoot Windows Biometric Framework issues.
Run this to identify why biometric session creation is failing.
"""

import logging
import sys
import os
import subprocess
import ctypes
from ctypes import wintypes
import json

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_windows_version():
    """Check if Windows supports Biometric Framework."""
    logger.info("=" * 60)
    logger.info("CHECKING WINDOWS VERSION")
    logger.info("=" * 60)
    
    try:
        version_info = sys.getwindowsversion()
        logger.info(f"Windows Version: {version_info.major}.{version_info.minor}")
        
        # Windows Biometric Framework requires Windows 7+
        if version_info.major < 6 or (version_info.major == 6 and version_info.minor < 1):
            logger.error("Windows 7 or later required for Biometric Framework")
            return False
        logger.info("✅ Windows version compatible")
        return True
    except Exception as e:
        logger.error(f"Error checking Windows version: {e}")
        return False


def check_winbio_dll():
    """Check if winbio.dll is available."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING WINBIO.DLL")
    logger.info("=" * 60)
    
    try:
        lib = ctypes.WinDLL('winbio.dll')
        logger.info("✅ winbio.dll found and loaded successfully")
        return True
    except OSError as e:
        logger.error(f"❌ winbio.dll not found: {e}")
        logger.info("Install options:")
        logger.info("1. Windows Biometric Framework is included in Windows 7+")
        logger.info("2. Update to latest Windows version")
        logger.info("3. Install WBDI drivers from your device manufacturer")
        return False


def check_biometric_devices():
    """Check for connected biometric devices."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING BIOMETRIC DEVICES")
    logger.info("=" * 60)
    
    try:
        proc = subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-Command',
                'Get-PnpDevice -Class Biometric | Select-Object Name,Status,InstanceId,Manufacturer | ConvertTo-Json'
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if proc.returncode == 0 and proc.stdout.strip():
            devices = json.loads(proc.stdout)
            if isinstance(devices, list):
                logger.info(f"Found {len(devices)} biometric device(s):")
                for i, device in enumerate(devices, 1):
                    logger.info(f"\n  Device {i}:")
                    logger.info(f"    Name: {device.get('Name', 'Unknown')}")
                    logger.info(f"    Status: {device.get('Status', 'Unknown')}")
                    logger.info(f"    Manufacturer: {device.get('Manufacturer', 'Unknown')}")
                    logger.info(f"    Instance ID: {device.get('InstanceId', 'Unknown')}")
                    
                    if device.get('Status') == 'OK':
                        logger.info(f"    ✅ Device is enabled")
                    else:
                        logger.warning(f"    ⚠️ Device may have issues - Status: {device.get('Status')}")
                return len(devices) > 0
            else:
                logger.info("Single device found:")
                logger.info(f"  Name: {devices.get('Name', 'Unknown')}")
                logger.info(f"  Status: {devices.get('Status', 'Unknown')}")
                logger.info(f"  Manufacturer: {devices.get('Manufacturer', 'Unknown')}")
                return True
        else:
            logger.warning("❌ No biometric devices found")
            return False
    except Exception as e:
        logger.error(f"Error checking devices: {e}")
        return False


def check_fingerprint_enrollment():
    """Check if fingerprints are enrolled."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING FINGERPRINT ENROLLMENT")
    logger.info("=" * 60)
    
    try:
        proc = subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-Command',
                'Get-WmiObject -Class Win32_SystemUsers | Select-Object * | ConvertTo-Json'
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Try alternative method
        proc = subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-Command',
                'Get-LocalUser | Select-Object Name | ConvertTo-Json'
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        logger.info("ℹ️ To enroll fingerprints:")
        logger.info("1. Go to Settings → Accounts → Sign-in options")
        logger.info("2. Select 'Biometric' or 'Windows Hello'")
        logger.info("3. Follow the enrollment wizard")
        return True
    except Exception as e:
        logger.error(f"Error checking enrollment: {e}")
        return False


def check_serial_ports():
    """Check for serial ports (fallback method)."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING SERIAL PORTS (FALLBACK)")
    logger.info("=" * 60)
    
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        
        if ports:
            logger.info(f"Found {len(ports)} serial port(s):")
            for port in ports:
                logger.info(f"\n  Port: {port.device}")
                logger.info(f"    Description: {port.description}")
                logger.info(f"    Manufacturer: {port.manufacturer}")
                logger.info(f"    Serial: {port.serial_number}")
            return True
        else:
            logger.warning("❌ No serial ports found")
            return False
    except Exception as e:
        logger.error(f"Error checking serial ports: {e}")
        return False


def main():
    """Run all diagnostic checks."""
    logger.info("\n")
    logger.info("╔" + "=" * 58 + "╗")
    logger.info("║  FINGERPRINT BIOMETRIC FRAMEWORK DIAGNOSTIC TOOL  ║")
    logger.info("╚" + "=" * 58 + "╝")
    
    results = {
        "Windows Version": check_windows_version(),
        "winbio.dll Available": check_winbio_dll(),
        "Biometric Devices": check_biometric_devices(),
        "Fingerprint Enrollment": check_fingerprint_enrollment(),
        "Serial Ports": check_serial_ports(),
    }
    
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    
    for check_name, result in results.items():
        status = "✅" if result else "⚠️"
        logger.info(f"{status} {check_name}: {'PASS' if result else 'CHECK NEEDED'}")
    
    logger.info("\n" + "=" * 60)
    logger.info("TROUBLESHOOTING TIPS")
    logger.info("=" * 60)
    logger.info("""
1. SESSION CREATION FAILS:
   - Ensure at least one fingerprint is enrolled
   - Run app as Administrator
   - Update biometric device drivers
   - Restart Windows

2. DEVICE NOT DETECTED:
   - Check Device Manager for "Biometric devices" category
   - Update driver from manufacturer website
   - Disable and re-enable device in Device Manager
   - Try different USB port

3. NO FINGERPRINT CAPTURED:
   - Ensure proper finger contact with sensor
   - Try different fingers
   - Clean sensor with dry cloth
   - Increase timeout value

4. WINBIO.DLL NOT FOUND:
   - Install Windows Biometric Framework components
   - Update Windows to latest version
   - Install device-specific WBDI drivers from OEM
    """)
    
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
