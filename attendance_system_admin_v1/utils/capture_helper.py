import sys
import os
import argparse
import json
import logging
import threading
import ctypes
from ctypes import wintypes
import tkinter as tk

# Add grandparent (project root) directory to path to import modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
from modules.biometric_engine import WindowsBiometricEngine, HRESULT, S_OK, format_hresult

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("capture_helper")

def write_result(filepath, data):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to write result file {filepath}: {e}")

class BiometricDialog:
    def __init__(self, device_id, timeout_ms, output_file, engine):
        self.device_id = device_id
        self.timeout_ms = timeout_ms
        self.output_file = output_file
        self.engine = engine
        self.result = None
        self.capture_thread = None
        
        self.root = tk.Tk()
        self.root.title("Biometric Verification")
        
        # Geometry and position
        width, height = 360, 260
        self.root.geometry(f"{width}x{height}")
        self.root.resizable(False, False)
        
        # Center the window on the screen
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.root.geometry(f"+{x}+{y}")
        
        # Stylings
        self.root.configure(bg="#F9FAFB")
        
        # Force active focus & stay on top
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        
        # UI Elements
        title_font = ("Segoe UI", 13, "bold")
        self.title_label = tk.Label(
            self.root, 
            text="Fingerprint Authentication", 
            font=title_font, 
            bg="#F9FAFB", 
            fg="#111827"
        )
        self.title_label.pack(pady=(25, 5))
        
        desc_font = ("Segoe UI", 10)
        self.desc_label = tk.Label(
            self.root, 
            text="Please swipe/place your enrolled finger\nacross the fingerprint scanner.", 
            font=desc_font, 
            bg="#F9FAFB", 
            fg="#4B5563"
        )
        self.desc_label.pack(pady=5)
        
        self.icon_label = tk.Label(
            self.root, 
            text="👆", 
            font=("Segoe UI", 48), 
            bg="#F9FAFB", 
            fg="#3B82F6"
        )
        self.icon_label.pack(pady=10)
        
        self.cancel_button = tk.Button(
            self.root, 
            text="Cancel", 
            font=("Segoe UI", 9), 
            command=self.cancel_scan,
            bg="#EF4444",
            fg="white",
            activebackground="#DC2626",
            activeforeground="white",
            relief="flat",
            padx=15,
            pady=5
        )
        self.cancel_button.pack(pady=(10, 0))
        
        # Start the background capture thread after window is drawn
        self.root.after(100, self.start_capture_thread)
        
    def start_capture_thread(self):
        self.capture_thread = threading.Thread(target=self.run_capture)
        self.capture_thread.daemon = True
        self.capture_thread.start()
        
    def run_capture(self):
        try:
            capture_res = self.engine.capture_fingerprint(timeout_ms=self.timeout_ms)
            if capture_res:
                position, sample_size, username = capture_res
                self.result = {
                    "status": "success",
                    "device_unit_id": position,
                    "finger_position_code": sample_size,
                    "username": username,
                    # Legacy keys for backward compatibility
                    "fingerprint_position": position,
                    "sample_size": sample_size
                }
                self.root.after(0, self.show_success)
            else:
                self.result = {
                    "status": "timeout",
                    "error": "Capture failed or timed out."
                }
                self.root.after(0, self.show_timeout)
        except Exception as e:
            self.result = {
                "status": "error",
                "error": str(e)
            }
            self.root.after(0, self.close_with_error)
            
    def show_success(self):
        self.icon_label.config(text="✅", fg="#10B981")
        self.desc_label.config(text="Success! Fingerprint identified.", fg="#10B981")
        self.cancel_button.pack_forget()
        self.root.update()
        write_result(self.output_file, self.result)
        self.root.after(300, self.root.destroy)
        
    def show_timeout(self):
        self.icon_label.config(text="❌", fg="#EF4444")
        self.desc_label.config(text="Capture failed or timed out.", fg="#EF4444")
        self.cancel_button.pack_forget()
        self.root.update()
        write_result(self.output_file, self.result)
        self.root.after(300, self.root.destroy)
        
    def close_with_error(self):
        write_result(self.output_file, self.result)
        self.root.destroy()
        
    def cancel_scan(self):
        if self.engine:
            try:
                WinBioCancel = self.engine.winbio_lib.WinBioCancel
                WinBioCancel.argtypes = [ctypes.c_void_p]
                WinBioCancel.restype = HRESULT
                WinBioCancel(self.engine.session_handle)
            except Exception:
                pass
        self.result = {
            "status": "timeout",
            "error": "Scan cancelled by user."
        }
        write_result(self.output_file, self.result)
        self.root.destroy()

def main():
    parser = argparse.ArgumentParser(description="Windows Biometric Framework Fingerprint Capture Helper")
    parser.add_argument("--device-id", required=True, help="Instance ID of target biometric device")
    parser.add_argument("--output-file", required=True, help="Path to write JSON result to")
    parser.add_argument("--timeout", type=int, default=15000, help="Timeout in milliseconds")
    
    args = parser.parse_args()
    
    engine = WindowsBiometricEngine()
    
    # Check enrollments
    try:
        enrollments = engine.get_enrollment_count(args.device_id)
        if enrollments == 0:
            result = {
                "status": "error",
                "error": "No fingerprints enrolled on this device in Windows Hello. Please enroll first in Windows Settings."
            }
            write_result(args.output_file, result)
            sys.exit(1)
    except Exception as e:
        logger.warning(f"Could not check enrollment count: {e}")
        
    # Create biometric session
    success, msg = engine.create_session(args.device_id)
    if not success:
        result = {
            "status": "error",
            "error": f"Failed to create biometric session: {msg}"
        }
        write_result(args.output_file, result)
        sys.exit(1)
        
    # Start the dialog
    dialog = BiometricDialog(args.device_id, args.timeout, args.output_file, engine)
    dialog.root.mainloop()
    
    engine.close_session()

if __name__ == "__main__":
    main()
