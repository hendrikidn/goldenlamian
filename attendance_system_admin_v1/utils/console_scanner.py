import sys
import time
import logging
import os

# Add grandparent (project root) directory to path to import modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from modules.biometric_engine import WindowsBiometricEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    print("=" * 60)
    print("        NATIVE CONSOLE FINGERPRINT SCANNER (NO BROWSER)")
    print("=============================================================")
    print("This console app runs directly in the foreground, which is")
    print("required by the Windows Biometric Framework for security.")
    print("=" * 60)
    
    engine = WindowsBiometricEngine()
    devices = engine.get_available_devices()
    
    if not devices:
        print("❌ No biometric devices found.")
        return
        
    print("\nAvailable Devices:")
    for i, dev in enumerate(devices):
        print(f"  [{i}] {dev.get('Name')} (Status: {dev.get('Status')})")
        
    try:
        choice = int(input("\nSelect device index to start: "))
        selected_device = devices[choice]
    except Exception:
        print("Invalid choice, using default.")
        selected_device = devices[0]
        
    device_id = selected_device.get('InstanceId')
    print(f"\nInitializing session on: {selected_device.get('Name')}...")
    success, msg = engine.create_session(device_id)
    
    if not success:
        print(f"❌ Failed to create session: {msg}")
        return
        
    print(f"\n✅ Session created successfully.")
    
    # Check enrollment count
    enrollments = engine.get_enrollment_count(device_id)
    print(f"📊 Enrolled fingerprints found on this device: {enrollments}")
    if enrollments == 0:
        print("⚠️ Warning: No fingerprints enrolled for this device in Windows Hello.")
        print("Please enroll a fingerprint in Windows settings before scanning.")
        engine.close_session()
        return
        
    print("\n" + "=" * 60)
    print("👉 READY: PLACE/SWIPE YOUR FINGER ON THE SCANNER NOW!")
    print("=============================================================")
    
    # We call capture_fingerprint with a 15-second timeout
    result = engine.capture_fingerprint(timeout_ms=15000)
    
    if result:
        unit_id, sub_factor = result[0], result[1]
        username = result[2] if len(result) > 2 else "Unknown"
        print(f"\n🎉 SUCCESS: Fingerprint identified!")
        print(f"  Unit ID: {unit_id}")
        print(f"  Sub-factor ID: {sub_factor}")
        print(f"  Windows User: {username}")
    else:
        print(f"\n❌ Capture failed or timed out.")
        
    engine.close_session()

if __name__ == '__main__':
    main()
