import ctypes
from ctypes import wintypes
import logging
import os
from typing import Optional, Tuple, List, Dict
import subprocess
import json

logger = logging.getLogger(__name__)

# Compatibility for HRESULT on all Python versions
HRESULT = getattr(wintypes, 'HRESULT', ctypes.c_long)
WINBIO_SESSION_HANDLE = ctypes.c_void_p
WINBIO_UNIT_ID = wintypes.DWORD
WINBIO_REJECT_DETAIL = wintypes.DWORD

# Windows Biometric Framework API constants
WINBIO_TYPE_FINGERPRINT = 0x00000008
WINBIO_SENSOR_SUBTYPE_FINGERPRINT_SWIPE = 1
WINBIO_SENSOR_SUBTYPE_FINGERPRINT_TOUCH = 2

# Session flags
WINBIO_FLAG_DEFAULT = 0x00000000
WINBIO_FLAG_RAW = 0x00000001
WINBIO_FLAG_ADVANCED = 0x00000002

# Pool types
WINBIO_POOL_SYSTEM = 0x00000001
WINBIO_POOL_PRIVATE = 0x00000002

# Capture purpose and data flags
WINBIO_NO_PURPOSE_AVAILABLE = 0x00000000
WINBIO_DATA_FLAG_RAW = 0x00000001
WINBIO_DATA_FLAG_PROCESSED = 0x00000002
WINBIO_DATA_FLAG_SIGNED = 0x00000004
WINBIO_DATA_FLAG_PRIVACY = 0x00000020
WINBIO_DATA_FLAG_INTEGRITY = 0x00000040

# Identity types
WINBIO_ID_TYPE_NULL = 0x00000000
WINBIO_ID_TYPE_WILDCARD = 0x00000001
WINBIO_ID_TYPE_GUID = 0x00000002
WINBIO_ID_TYPE_SID = 0x00000003

# HRESULT codes
S_OK = 0
E_ACCESSDENIED = 0x80070005
WINBIO_E_UNSUPPORTED_FACTOR = 0x80098001
WINBIO_E_DEVICE_NOT_FOUND = 0x80098001  # Alias for compatibility
WINBIO_E_INVALID_UNIT = 0x80098002
WINBIO_E_UNKNOWN_ID = 0x80098003
WINBIO_E_CANCELED = 0x80098004
WINBIO_E_NO_MATCH = 0x80098005
WINBIO_E_CAPTURE_ABORTED = 0x80098006
WINBIO_E_ENROLLMENT_IN_PROGRESS = 0x80098007
WINBIO_E_BAD_CAPTURE = 0x80098008

# Error message mapping
HRESULT_MESSAGES = {
    S_OK: "Success",
    E_ACCESSDENIED: "Access denied",
    WINBIO_E_UNSUPPORTED_FACTOR: "Unsupported factor",
    WINBIO_E_INVALID_UNIT: "Invalid unit ID",
    WINBIO_E_UNKNOWN_ID: "Unknown fingerprint identity (not enrolled)",
    WINBIO_E_CANCELED: "Operation canceled",
    WINBIO_E_NO_MATCH: "No match found",
    WINBIO_E_CAPTURE_ABORTED: "Capture aborted",
    WINBIO_E_ENROLLMENT_IN_PROGRESS: "Enrollment in progress",
    WINBIO_E_BAD_CAPTURE: "Bad fingerprint capture (poor quality or wrong placement)",
}


WINBIO_STRING = ctypes.c_wchar * 256


class WINBIO_VERSION(ctypes.Structure):
    _fields_ = [
        ("MajorVersion", wintypes.DWORD),
        ("MinorVersion", wintypes.DWORD),
    ]


class WINBIO_UNIT_SCHEMA(ctypes.Structure):
    _fields_ = [
        ("UnitId", WINBIO_UNIT_ID),
        ("PoolType", wintypes.DWORD),
        ("BiometricFactor", wintypes.DWORD),
        ("SensorSubType", wintypes.DWORD),
        ("Capabilities", wintypes.DWORD),
        ("DeviceInstanceId", WINBIO_STRING),
        ("Description", WINBIO_STRING),
        ("Manufacturer", WINBIO_STRING),
        ("Model", WINBIO_STRING),
        ("SerialNumber", WINBIO_STRING),
        ("FirmwareVersion", WINBIO_VERSION),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WINBIO_IDENTITY_NULL(ctypes.Structure):
    _fields_ = [("Reserved", wintypes.DWORD)]


class WINBIO_IDENTITY_WILDCARD(ctypes.Structure):
    _fields_ = [("Reserved", wintypes.DWORD)]


class WINBIO_IDENTITY_SID(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),
        ("Data", ctypes.c_ubyte * 68),
    ]


class WINBIO_IDENTITY_UNION(ctypes.Union):
    _fields_ = [
        ("Null", WINBIO_IDENTITY_NULL),
        ("Wildcard", WINBIO_IDENTITY_WILDCARD),
        ("TemplateGuid", GUID),
        ("AccountSid", WINBIO_IDENTITY_SID),
    ]


class WINBIO_IDENTITY(ctypes.Structure):
    _fields_ = [
        ("Type", wintypes.DWORD),
        ("Value", WINBIO_IDENTITY_UNION),
    ]


def format_hresult(result: int) -> int:
    """Normalize HRESULT to unsigned 32-bit value for lookup and display."""
    return ctypes.c_uint32(result).value


def is_user_admin() -> bool:
    """Return True when running with Windows Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


class WinBioEngineError(Exception):
    """Exception for Windows Biometric Engine errors."""
    pass


class WindowsBiometricEngine:
    """Interface to Windows Biometric Framework."""
    
    def __init__(self):
        """Initialize Windows Biometric Engine."""
        self.session_handle = None
        self.winbio_lib = None
        self.selected_device = None
        self.selected_unit_id = None
        self._load_library()
    
    def _load_library(self):
        """Load the Windows Biometric Framework library."""
        try:
            self.winbio_lib = ctypes.WinDLL('winbio.dll')
            logger.info("Windows Biometric Framework library loaded successfully.")
        except OSError:
            logger.warning("winbio.dll not found. Windows Biometric Framework may not be installed.")
            self.winbio_lib = None
    
    @staticmethod
    def get_available_devices() -> List[Dict]:
        """Get list of all available biometric devices."""
        devices = []
        try:
            # Try native WinBio enumeration first (extremely fast)
            try:
                import ctypes
                from ctypes import wintypes
                
                winbio_lib = ctypes.WinDLL('winbio.dll')
                
                class WINBIO_VERSION(ctypes.Structure):
                    _fields_ = [
                        ("MajorVersion", wintypes.DWORD),
                        ("MinorVersion", wintypes.DWORD),
                    ]
                
                WINBIO_STRING = ctypes.c_wchar * 256
                
                class WINBIO_UNIT_SCHEMA(ctypes.Structure):
                    _fields_ = [
                        ("UnitId", wintypes.DWORD),
                        ("PoolType", wintypes.DWORD),
                        ("BiometricFactor", wintypes.DWORD),
                        ("SensorSubType", wintypes.DWORD),
                        ("Capabilities", wintypes.DWORD),
                        ("DeviceInstanceId", WINBIO_STRING),
                        ("Description", WINBIO_STRING),
                        ("Manufacturer", WINBIO_STRING),
                        ("Model", WINBIO_STRING),
                        ("SerialNumber", WINBIO_STRING),
                        ("FirmwareVersion", WINBIO_VERSION),
                    ]
                
                WinBioEnumBiometricUnits = winbio_lib.WinBioEnumBiometricUnits
                WinBioEnumBiometricUnits.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(WINBIO_UNIT_SCHEMA)), ctypes.POINTER(ctypes.c_size_t)]
                WinBioEnumBiometricUnits.restype = wintypes.DWORD
                
                unit_array = ctypes.POINTER(WINBIO_UNIT_SCHEMA)()
                unit_count = ctypes.c_size_t(0)
                
                result = WinBioEnumBiometricUnits(0x00000008, ctypes.byref(unit_array), ctypes.byref(unit_count))
                if result == 0:
                    for index in range(unit_count.value):
                        unit = unit_array[index]
                        devices.append({
                            'Name': unit.Description,
                            'Status': 'OK',
                            'InstanceId': unit.DeviceInstanceId,
                            'DeviceID': unit.DeviceInstanceId
                        })
                    
                    WinBioFree = winbio_lib.WinBioFree
                    WinBioFree.argtypes = [ctypes.c_void_p]
                    WinBioFree.restype = None
                    if unit_array:
                        WinBioFree(unit_array)
                    
                logger.info(f"Natively queried biometric units: found {len(devices)} device(s)")
                return devices
            except Exception as e:
                logger.warning(f"Native WinBio device enumeration failed, falling back: {e}")

            # Fallback to PowerShell
            proc = subprocess.run(
                [
                    'powershell.exe',
                    '-NoProfile',
                    '-Command',
                    'Get-PnpDevice -Class Biometric | Select-Object Name,Status,InstanceId,DeviceID | ConvertTo-Json'
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                if isinstance(data, list):
                    devices = data
                else:
                    devices = [data]
            
            logger.info(f"Found {len(devices)} biometric device(s) via fallback PowerShell")
            return devices
        except Exception as e:
            logger.error(f"Error getting available devices: {e}")
            return []
    
    @staticmethod
    def get_available_serial_ports() -> List[Dict]:
        """Get list of available serial ports that might be biometric devices."""
        ports = []
        try:
            import serial
            import serial.tools.list_ports
            for port_info in serial.tools.list_ports.comports():
                ports.append({
                    'port': port_info.device,
                    'manufacturer': port_info.manufacturer or 'Unknown',
                    'description': port_info.description or 'Unknown',
                    'serial_number': port_info.serial_number or 'N/A'
                })
            logger.info(f"Found {len(ports)} serial port(s)")
            return ports
        except Exception as e:
            logger.error(f"Error getting serial ports: {e}")
            return []
    
    def set_device(self, device_id: Optional[str] = None):
        """Set the device to use for this session.
        
        Args:
            device_id: Device instance ID or None to use default
        """
        self.selected_device = device_id
        logger.info(f"Selected device: {device_id or 'Default'}")

    def _enumerate_units(self) -> list:
        """Enumerate available biometric units through WinBio."""
        units = []
        try:
            WinBioEnumBiometricUnits = self.winbio_lib.WinBioEnumBiometricUnits
            WinBioEnumBiometricUnits.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(WINBIO_UNIT_SCHEMA)), ctypes.POINTER(ctypes.c_size_t)]
            WinBioEnumBiometricUnits.restype = HRESULT

            unit_array = ctypes.POINTER(WINBIO_UNIT_SCHEMA)()
            unit_count = ctypes.c_size_t(0)

            result = WinBioEnumBiometricUnits(WINBIO_TYPE_FINGERPRINT, ctypes.byref(unit_array), ctypes.byref(unit_count))
            if result != S_OK:
                return []

            for index in range(unit_count.value):
                unit = unit_array[index]
                units.append({
                    'UnitId': unit.UnitId,
                    'DeviceInstanceId': unit.DeviceInstanceId,
                    'Description': unit.Description,
                    'Manufacturer': unit.Manufacturer,
                    'Model': unit.Model,
                    'SerialNumber': unit.SerialNumber,
                    'SensorSubType': unit.SensorSubType,
                    'PoolType': unit.PoolType,
                })

            WinBioFree = self.winbio_lib.WinBioFree
            WinBioFree.argtypes = [ctypes.c_void_p]
            WinBioFree.restype = None
            if unit_array:
                WinBioFree(unit_array)

        except Exception as e:
            logger.warning(f"Failed to enumerate biometric units: {e}")

        return units

    def _find_unit_id_for_device(self, device_instance_id: str) -> Optional[int]:
        """Find the WinBio UnitId for a given device instance ID."""
        units = self._enumerate_units()
        for unit in units:
            if unit.get('DeviceInstanceId') and unit['DeviceInstanceId'].lower() == device_instance_id.lower():
                return unit['UnitId']
        return None

    def create_session(self, device_id: Optional[str] = None) -> Tuple[bool, str]:
        """Create a Windows biometric session.
        
        Args:
            device_id: Optional device ID to use
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not self.winbio_lib:
                msg = "Windows Biometric Framework not available. Ensure Windows 7+ with biometric drivers."
                logger.error(msg)
                return False, msg
            
            WinBioOpenSession = self.winbio_lib.WinBioOpenSession
            WinBioOpenSession.argtypes = [
                wintypes.DWORD,           # WINBIO_BIOMETRIC_TYPE Factor
                wintypes.DWORD,           # WINBIO_POOL_TYPE PoolType
                wintypes.DWORD,           # WINBIO_SESSION_FLAGS Flags
                ctypes.POINTER(WINBIO_UNIT_ID),  # UnitArray or NULL
                ctypes.c_size_t,          # UnitCount
                ctypes.c_void_p,          # DatabaseId pointer or NULL
                ctypes.POINTER(WINBIO_SESSION_HANDLE)
            ]
            WinBioOpenSession.restype = HRESULT
            
            session_handle = WINBIO_SESSION_HANDLE()

            self.selected_unit_id = None
            if device_id:
                selected_unit_id = self._find_unit_id_for_device(device_id)
                if selected_unit_id is None:
                    msg = f"Unable to map device InstanceId to a WinBio unit: {device_id}"
                    logger.error(msg)
                    return False, msg
                self.selected_unit_id = selected_unit_id

            result = WinBioOpenSession(
                WINBIO_TYPE_FINGERPRINT,
                WINBIO_POOL_SYSTEM,
                WINBIO_FLAG_DEFAULT,
                None,
                0,
                None,
                ctypes.byref(session_handle)
            )
            
            if result != S_OK:
                result_code = format_hresult(result)
                error_msg = HRESULT_MESSAGES.get(result_code, f"Unknown error (0x{result_code:08X})")
                msg = f"WinBioOpenSession failed: {error_msg} (Code: 0x{result_code:08X})"
                logger.error(msg)
                
                if result_code == WINBIO_E_DEVICE_NOT_FOUND:
                    msg += " - No biometric device found. Check Device Manager."
                elif result_code == WINBIO_E_INVALID_UNIT:
                    msg += " - Invalid device unit. Confirm the selected device is a fingerprint sensor."
                
                return False, msg
            
            self.session_handle = session_handle
            self.selected_device = device_id or "default"
            success_msg = f"Biometric session created successfully (Device: {self.selected_device})"
            logger.info(success_msg)
            return True, success_msg
            
        except Exception as e:
            msg = f"Error creating biometric session: {str(e)}"
            logger.error(msg)
            return False, msg
    
    def capture_fingerprint(self, timeout_ms: int = 30000) -> Optional[Tuple[int, int, str]]:
        """Capture and identify a fingerprint sample.
        
        Returns:
            Tuple of (unit_id, sub_factor, username) or None if capture failed
        """
        import threading
        
        try:
            if not self.session_handle:
                logger.error("No active biometric session.")
                return None
            
            WinBioIdentify = self.winbio_lib.WinBioIdentify
            WinBioIdentify.argtypes = [
                WINBIO_SESSION_HANDLE,
                ctypes.POINTER(WINBIO_UNIT_ID),
                ctypes.POINTER(WINBIO_IDENTITY),
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(WINBIO_REJECT_DETAIL)
            ]
            WinBioIdentify.restype = HRESULT
            
            unit_id = WINBIO_UNIT_ID()
            identity = WINBIO_IDENTITY()
            sub_factor = wintypes.DWORD()
            reject_detail = WINBIO_REJECT_DETAIL()
            
            identify_result = [None]
            
            def worker():
                try:
                    result = WinBioIdentify(
                        self.session_handle,
                        ctypes.byref(unit_id),
                        ctypes.byref(identity),
                        ctypes.byref(sub_factor),
                        ctypes.byref(reject_detail)
                    )
                    identify_result[0] = (result, unit_id.value, sub_factor.value, reject_detail.value)
                except Exception as ex:
                    logger.error(f"Error in WinBioIdentify worker thread: {ex}")
                    identify_result[0] = (None, None, None, None)

            t = threading.Thread(target=worker)
            t.daemon = True
            t.start()
            
            # Wait for the thread to complete, with timeout
            t.join(timeout=timeout_ms / 1000.0)
            
            if t.is_alive():
                logger.warning(f"WinBioIdentify timed out after {timeout_ms} ms. Cancelling...")
                try:
                    WinBioCancel = self.winbio_lib.WinBioCancel
                    WinBioCancel.argtypes = [WINBIO_SESSION_HANDLE]
                    WinBioCancel.restype = HRESULT
                    
                    cancel_res = WinBioCancel(self.session_handle)
                    if cancel_res != S_OK:
                        logger.error(f"WinBioCancel returned non-zero code: 0x{format_hresult(cancel_res):08X}")
                except Exception as ex:
                    logger.error(f"Error calling WinBioCancel: {ex}")
                
                # Wait briefly for worker thread to exit
                t.join(timeout=2.0)
                return None
                
            if identify_result[0] is None:
                return None
                
            result, unit_val, sub_factor_val, reject_detail_val = identify_result[0]
            
            if result != S_OK:
                result_code = format_hresult(result)
                error_msg = HRESULT_MESSAGES.get(result_code, f"Unknown error (0x{result_code:08X})")
                if result_code == E_ACCESSDENIED:
                    logger.warning("WinBioIdentify failed: access denied. Please run as Administrator and ensure biometric services are enabled.")
                elif result_code == WINBIO_E_UNKNOWN_ID:
                    logger.warning("WinBioIdentify returned unknown identity. The fingerprint is not enrolled.")
                elif result_code == WINBIO_E_BAD_CAPTURE:
                    logger.warning(f"WinBioIdentify returned bad capture. RejectDetail={reject_detail_val}")
                elif result_code == WINBIO_E_CANCELED:
                    logger.info("WinBioIdentify operation was canceled successfully.")
                else:
                    logger.warning(f"WinBioIdentify failed: {error_msg} (Code: 0x{result_code:08X}), RejectDetail={reject_detail_val}")
                return None

            if self.selected_unit_id is not None and unit_val != self.selected_unit_id:
                logger.warning(f"Fingerprint identified on unit {unit_val}, but selected unit is {self.selected_unit_id}. Ignoring.")
                return None

            username = self.get_username_from_identity(identity)
            logger.info(f"Fingerprint identified - Unit ID: {unit_val}, SubFactor: {sub_factor_val}, User: {username}")
            return (unit_val, sub_factor_val, username)
            
        except Exception as e:
            logger.error(f"Error capturing fingerprint: {e}")
            return None

    def get_username_from_identity(self, identity: WINBIO_IDENTITY) -> str:
        """Resolve WINBIO_IDENTITY to a Windows username. Falls back to current user if failed."""
        if identity.Type != 3:  # Not WINBIO_ID_TYPE_SID
            return os.environ.get('USERNAME', 'Unknown')
            
        try:
            advapi32 = ctypes.WinDLL('advapi32.dll')
            LookupAccountSid = advapi32.LookupAccountSidW
            LookupAccountSid.argtypes = [
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD)
            ]
            LookupAccountSid.restype = wintypes.BOOL
            
            name_len = wintypes.DWORD(256)
            domain_len = wintypes.DWORD(256)
            name_buf = ctypes.create_unicode_buffer(name_len.value)
            domain_buf = ctypes.create_unicode_buffer(domain_len.value)
            sid_type = wintypes.DWORD()
            
            sid_ptr = ctypes.byref(identity.Value.AccountSid.Data)
            
            res = LookupAccountSid(
                None,
                sid_ptr,
                name_buf,
                ctypes.byref(name_len),
                domain_buf,
                ctypes.byref(domain_len),
                ctypes.byref(sid_type)
            )
            if res:
                return name_buf.value
        except Exception as e:
            logger.warning(f"Error resolving SID to username: {e}")
            
        return os.environ.get('USERNAME', 'Unknown')
    
    def get_enrollment_count(self, device_id: str) -> int:
        """Get the number of enrolled fingerprints for the current user on the specified device.
        
        Args:
            device_id: Device instance ID
            
        Returns:
            Number of enrolled fingers, or 0 if none or failed
        """
        try:
            if not self.winbio_lib:
                return 0
                
            # Find the unit ID
            unit_id = self._find_unit_id_for_device(device_id)
            if unit_id is None:
                logger.warning(f"Could not map device ID {device_id} to a biometric unit ID.")
                return 0
                
            # Retrieve current user's identity (SID)
            identity = self._get_current_user_identity()
            if identity is None:
                logger.warning("Could not resolve current user's SID.")
                return 0
                
            # Create a temporary session if not already open
            temp_session = False
            session_handle = self.session_handle
            if not session_handle:
                # Open a temporary session for checking
                WinBioOpenSession = self.winbio_lib.WinBioOpenSession
                WinBioOpenSession.argtypes = [
                    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                    ctypes.POINTER(WINBIO_UNIT_ID), ctypes.c_size_t,
                    ctypes.c_void_p, ctypes.POINTER(WINBIO_SESSION_HANDLE)
                ]
                WinBioOpenSession.restype = HRESULT
                
                temp_handle = WINBIO_SESSION_HANDLE()
                res = WinBioOpenSession(
                    WINBIO_TYPE_FINGERPRINT,
                    WINBIO_POOL_SYSTEM,
                    WINBIO_FLAG_DEFAULT,
                    None, 0, None,
                    ctypes.byref(temp_handle)
                )
                if res != S_OK:
                    logger.warning(f"Failed to open temporary session for enrollment check: 0x{format_hresult(res):08X}")
                    return 0
                session_handle = temp_handle
                temp_session = True
                
            # Call WinBioEnumEnrollments
            WinBioEnumEnrollments = self.winbio_lib.WinBioEnumEnrollments
            WinBioEnumEnrollments.argtypes = [
                WINBIO_SESSION_HANDLE,
                wintypes.DWORD,
                ctypes.POINTER(WINBIO_IDENTITY),
                ctypes.POINTER(ctypes.POINTER(wintypes.DWORD)),
                ctypes.POINTER(ctypes.c_size_t)
            ]
            WinBioEnumEnrollments.restype = HRESULT
            
            WinBioFree = self.winbio_lib.WinBioFree
            WinBioFree.argtypes = [ctypes.c_void_p]
            WinBioFree.restype = None
            
            sub_factor_array = ctypes.POINTER(wintypes.DWORD)()
            sub_factor_count = ctypes.c_size_t(0)
            
            res = WinBioEnumEnrollments(
                session_handle,
                unit_id,
                ctypes.byref(identity),
                ctypes.byref(sub_factor_array),
                ctypes.byref(sub_factor_count)
            )
            
            count = 0
            if res == S_OK:
                count = sub_factor_count.value
                if sub_factor_array:
                    WinBioFree(sub_factor_array)
            else:
                res_code = format_hresult(res)
                if res_code == WINBIO_E_UNKNOWN_ID:
                    # Unknown ID means 0 enrollments
                    count = 0
                else:
                    logger.warning(f"WinBioEnumEnrollments failed: 0x{res_code:08X}")
            
            if temp_session:
                WinBioCloseSession = self.winbio_lib.WinBioCloseSession
                WinBioCloseSession.argtypes = [WINBIO_SESSION_HANDLE]
                WinBioCloseSession.restype = HRESULT
                WinBioCloseSession(session_handle)
                
            return count
        except Exception as e:
            logger.warning(f"Error checking enrollment count: {e}")
            return 0

    def get_enrolled_finger_codes(self, device_id: str) -> List[int]:
        """Get list of enrolled fingerprint sub-factors (codes) for the current user."""
        try:
            if not self.winbio_lib:
                return []
                
            unit_id = self._find_unit_id_for_device(device_id)
            if unit_id is None:
                return []
                
            identity = self._get_current_user_identity()
            if identity is None:
                return []
                
            temp_session = False
            session_handle = self.session_handle
            if not session_handle:
                WinBioOpenSession = self.winbio_lib.WinBioOpenSession
                WinBioOpenSession.argtypes = [
                    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                    ctypes.POINTER(WINBIO_UNIT_ID), ctypes.c_size_t,
                    ctypes.c_void_p, ctypes.POINTER(WINBIO_SESSION_HANDLE)
                ]
                WinBioOpenSession.restype = HRESULT
                
                temp_handle = WINBIO_SESSION_HANDLE()
                res = WinBioOpenSession(
                    WINBIO_TYPE_FINGERPRINT,
                    WINBIO_POOL_SYSTEM,
                    WINBIO_FLAG_DEFAULT,
                    None, 0, None,
                    ctypes.byref(temp_handle)
                )
                if res != S_OK:
                    return []
                session_handle = temp_handle
                temp_session = True
                
            WinBioEnumEnrollments = self.winbio_lib.WinBioEnumEnrollments
            WinBioEnumEnrollments.argtypes = [
                WINBIO_SESSION_HANDLE,
                wintypes.DWORD,
                ctypes.POINTER(WINBIO_IDENTITY),
                ctypes.POINTER(ctypes.POINTER(wintypes.DWORD)),
                ctypes.POINTER(ctypes.c_size_t)
            ]
            WinBioEnumEnrollments.restype = HRESULT
            
            WinBioFree = self.winbio_lib.WinBioFree
            WinBioFree.argtypes = [ctypes.c_void_p]
            WinBioFree.restype = None
            
            sub_factor_array = ctypes.POINTER(wintypes.DWORD)()
            sub_factor_count = ctypes.c_size_t(0)
            
            res = WinBioEnumEnrollments(
                session_handle,
                unit_id,
                ctypes.byref(identity),
                ctypes.byref(sub_factor_array),
                ctypes.byref(sub_factor_count)
            )
            
            codes = []
            if res == S_OK:
                for i in range(sub_factor_count.value):
                    codes.append(sub_factor_array[i])
                if sub_factor_array:
                    WinBioFree(sub_factor_array)
            
            if temp_session:
                WinBioCloseSession = self.winbio_lib.WinBioCloseSession
                WinBioCloseSession.argtypes = [WINBIO_SESSION_HANDLE]
                WinBioCloseSession.restype = HRESULT
                WinBioCloseSession(session_handle)
                
            return codes
        except Exception as e:
            logger.warning(f"Error checking enrolled finger codes: {e}")
            return []
            
    def _get_current_user_identity(self) -> Optional[WINBIO_IDENTITY]:
        """Dynamically fetch the current Windows user's identity struct."""
        try:
            advapi32 = ctypes.WinDLL('advapi32.dll')
            kernel32 = ctypes.WinDLL('kernel32.dll')
            
            OpenProcessToken = advapi32.OpenProcessToken
            OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
            OpenProcessToken.restype = wintypes.BOOL
            
            GetTokenInformation = advapi32.GetTokenInformation
            GetTokenInformation.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD)
            ]
            GetTokenInformation.restype = wintypes.BOOL
            
            GetCurrentProcess = kernel32.GetCurrentProcess
            GetCurrentProcess.argtypes = []
            GetCurrentProcess.restype = ctypes.c_void_p
            
            TOKEN_QUERY = 0x0008
            token_handle = ctypes.c_void_p()
            if not OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token_handle)):
                return None
                
            needed_size = wintypes.DWORD(0)
            GetTokenInformation(token_handle, 1, None, 0, ctypes.byref(needed_size))
            
            if needed_size.value == 0:
                kernel32.CloseHandle(token_handle)
                return None
                
            buffer = (ctypes.c_ubyte * needed_size.value)()
            if not GetTokenInformation(token_handle, 1, buffer, needed_size.value, ctypes.byref(needed_size)):
                kernel32.CloseHandle(token_handle)
                return None
                
            kernel32.CloseHandle(token_handle)
            
            sid_ptr_val = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            
            GetLengthSid = advapi32.GetLengthSid
            GetLengthSid.argtypes = [ctypes.c_void_p]
            GetLengthSid.restype = wintypes.DWORD
            
            sid_len = GetLengthSid(sid_ptr_val)
            
            identity = WINBIO_IDENTITY()
            identity.Type = 3  # WINBIO_ID_TYPE_SID
            identity.Value.AccountSid.Size = sid_len
            ctypes.memmove(identity.Value.AccountSid.Data, sid_ptr_val, sid_len)
            return identity
        except Exception as e:
            logger.warning(f"Failed to fetch identity: {e}")
            return None

    def close_session(self):
        """Close the biometric session."""
        try:
            if self.session_handle and self.winbio_lib:
                WinBioCloseSession = self.winbio_lib.WinBioCloseSession
                WinBioCloseSession.argtypes = [WINBIO_SESSION_HANDLE]
                WinBioCloseSession.restype = HRESULT
                
                result = WinBioCloseSession(self.session_handle)
                if result == S_OK:
                    logger.info("Biometric session closed.")
                    self.session_handle = None
        except Exception as e:
            logger.error(f"Error closing biometric session: {e}")
    
    def __del__(self):
        """Cleanup on object destruction."""
        self.close_session()


def get_windows_device_id() -> str:
    """Retrieve the Windows MachineGuid from registry. Falls back to BIOS UUID or default."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(guid).strip().upper()
    except Exception:
        # Fallback to BIOS UUID via subprocess if registry fails
        try:
            import subprocess
            output = subprocess.check_output('wmic csproduct get uuid', shell=True).decode()
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            if len(lines) > 1:
                return lines[1].strip().upper()
        except Exception:
            pass
        return "UNKNOWN_DEVICE_ID"


def parse_fingerprint_id(fp_id: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parses fingerprint ID into (device_id, username, finger_code).
    Handles both new format (device_id_username_code) and legacy format (username_code).
    """
    parts = fp_id.split("_")
    if len(parts) >= 3:
        # Format: device_id_username_code
        device_id = parts[0]
        finger_code = parts[-1]
        username = "_".join(parts[1:-1])
        return device_id, username, finger_code
    elif len(parts) == 2:
        # Legacy format: username_code
        return None, parts[0], parts[1]
    else:
        # Fallback
        return None, None, fp_id

