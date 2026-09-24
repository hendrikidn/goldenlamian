import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional
import time
import json
import re
import threading
from pathlib import Path

def populate_distance_and_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Populate distance_meters and gps_accuracy columns from notes or aliases if empty."""
    if df is None or df.empty:
        return df

    df = df.copy()

    # Standardize column names (including distance_meter singular from Google Sheets)
    rename_dict = {}
    for col in df.columns:
        c_clean = str(col).strip().lower()
        if c_clean in ['distance_meter', 'distance', 'distance_m', 'distance (m)']:
            rename_dict[col] = 'distance_meters'
        elif c_clean in ['accuracy', 'gps_acc', 'gps accuracy']:
            rename_dict[col] = 'gps_accuracy'

    if rename_dict:
        df.rename(columns=rename_dict, inplace=True)

    if 'distance_meters' not in df.columns:
        df['distance_meters'] = ''
    if 'gps_accuracy' not in df.columns:
        df['gps_accuracy'] = ''

    dist_list = df['distance_meters'].astype(str).tolist()
    acc_list = df['gps_accuracy'].astype(str).tolist()
    notes_list = df['notes'].astype(str).tolist() if 'notes' in df.columns else [''] * len(df)

    re_dist = re.compile(r'Diluar radius\s*\(([^)]+)\)', re.IGNORECASE)

    new_dists = []
    new_accs = []
    for dist_val, acc_val, notes_val in zip(dist_list, acc_list, notes_list):
        d_val = dist_val.strip()
        a_val = acc_val.strip()

        if not d_val or d_val.lower() in ['nan', 'none']:
            match_dist = re_dist.search(notes_val)
            if match_dist:
                d_val = match_dist.group(1).strip()
            elif 'absen qr' in notes_val.lower():
                d_val = '0m (Dalam Radius)'

        if not a_val or a_val.lower() in ['nan', 'none']:
            if 'gps nonaktif' in notes_val.lower():
                a_val = 'Nonaktif'
            elif 'absen qr' in notes_val.lower():
                a_val = 'Akurat'

        new_dists.append(d_val)
        new_accs.append(a_val)

    df['distance_meters'] = new_dists
    df['gps_accuracy'] = new_accs

    return df


def populate_working_hours(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses working_hour (or notes) into work_start and work_end, and propagates 
    work_start and work_end across all attendance records for the same (NRP, date).
    Also populates distance_meters and gps_accuracy.
    """
    if df is None or df.empty or 'NRP' not in df.columns:
        return df

    df = populate_distance_and_accuracy(df)
    df = df.copy()

    if 'work_start' not in df.columns:
        df['work_start'] = ''
    if 'work_end' not in df.columns:
        df['work_end'] = ''

    re_jam = re.compile(r'\[Jam Kerja:\s*([^\]]+)\]', re.IGNORECASE)
    re_time = re.compile(r'(\d{1,2}[:\.]\d{2})\s*[-–—to]+\s*(\d{1,2}[:\.]\d{2})')

    ws_list = df['work_start'].astype(str).tolist()
    we_list = df['work_end'].astype(str).tolist()
    if 'working_hour' in df.columns:
        wh_list = df['working_hour'].astype(str).tolist()
    elif 'working_hours' in df.columns:
        wh_list = df['working_hours'].astype(str).tolist()
    else:
        wh_list = [''] * len(df)

    notes_list = df['notes'].astype(str).tolist() if 'notes' in df.columns else [''] * len(df)

    new_ws = []
    new_we = []

    for ws_val, we_val, wh_val, notes_val in zip(ws_list, we_list, wh_list, notes_list):
        ws_curr = ws_val.strip()
        we_curr = we_val.strip()
        if not ws_curr or not we_curr or ws_curr.lower() == 'nan' or we_curr.lower() == 'nan':
            wh = wh_val.strip()
            if not wh or wh.lower() == 'nan':
                match_notes = re_jam.search(notes_val)
                if match_notes:
                    wh = match_notes.group(1).strip()

            if wh and wh.lower() != 'nan':
                match = re_time.search(wh)
                if match:
                    s_time = match.group(1).replace('.', ':')
                    e_time = match.group(2).replace('.', ':')
                    if len(s_time) == 4: s_time = '0' + s_time
                    if len(e_time) == 4: e_time = '0' + e_time
                    ws_curr, we_curr = s_time, e_time
        new_ws.append(ws_curr if ws_curr.lower() != 'nan' else '')
        new_we.append(we_curr if we_curr.lower() != 'nan' else '')

    df['work_start'] = new_ws
    df['work_end'] = new_we

    # 2. Group by (NRP, date) to propagate CLOCK_IN hours to START_BREAK, END_BREAK, CLOCK_OUT
    if 'date' in df.columns:
        date_series = df['date'].astype(str).str.strip().str[:10].tolist()
        nrp_series = df['NRP'].astype(str).str.strip().tolist()
        type_series = df['type'].astype(str).str.upper().str.strip().tolist() if 'type' in df.columns else [''] * len(df)

        group_hours = {}
        # Pass 1: collect working hours (prefer CLOCK_IN)
        for nrp_k, date_k, type_k, ws_k, we_k in zip(nrp_series, date_series, type_series, new_ws, new_we):
            if ws_k and we_k:
                key = (nrp_k, date_k)
                if type_k == 'CLOCK_IN' or key not in group_hours:
                    group_hours[key] = (ws_k, we_k)

        # Pass 2: propagate
        final_ws = []
        final_we = []
        for nrp_k, date_k, ws_k, we_k in zip(nrp_series, date_series, new_ws, new_we):
            key = (nrp_k, date_k)
            if key in group_hours:
                ws_g, we_g = group_hours[key]
                final_ws.append(ws_g)
                final_we.append(we_g)
            else:
                final_ws.append(ws_k)
                final_we.append(we_k)

        df['work_start'] = final_ws
        df['work_end'] = final_we

    return df


def deduplicate_outlets_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicates outlet records by outlet name (case-insensitive).
    Merges non-empty values (Latitude, Longitude, Radius, Secret) from duplicate rows.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # Consolidate 'Nama Outlet' into 'Outlet' if present
    if 'Nama Outlet' in df.columns:
        if 'Outlet' not in df.columns:
            df['Outlet'] = ''
        df['Outlet'] = df.apply(
            lambda r: str(r['Outlet']).strip() if str(r.get('Outlet', '')).strip() and str(r.get('Outlet', '')).strip().lower() not in ['nan', 'none', '']
            else str(r.get('Nama Outlet', '')).strip(),
            axis=1
        )
        df.drop(columns=['Nama Outlet'], inplace=True, errors='ignore')

    # Drop 'Outlet ID' if present
    for c in ['Outlet ID', 'outlet_id', 'ID', 'id']:
        if c in df.columns:
            df.drop(columns=[c], inplace=True, errors='ignore')

    if 'Outlet' not in df.columns:
        return df

    df['Outlet'] = df['Outlet'].astype(str).str.strip()
    df = df[~df['Outlet'].isin(['', 'None', 'nan', '<NA>', 'N/A'])]

    # Group by lowercase Outlet name and merge duplicate rows
    merged_rows = []
    for _, group in df.groupby(df['Outlet'].str.lower(), sort=False):
        if len(group) == 1:
            merged_rows.append(group.iloc[0].to_dict())
        else:
            base_row = group.iloc[0].to_dict()
            for _, r in group.iloc[1:].iterrows():
                for col in group.columns:
                    base_val = str(base_row.get(col, '') or '').strip()
                    r_val = str(r.get(col, '') or '').strip()
                    if (not base_val or base_val.lower() in ['nan', 'none']) and r_val and r_val.lower() not in ['nan', 'none']:
                        base_row[col] = r_val
            merged_rows.append(base_row)

    df_clean = pd.DataFrame(merged_rows)
    std_cols = ['Outlet', 'Latitude', 'Longitude', 'Radius', 'Secret']
    existing_std = [c for c in std_cols if c in df_clean.columns]
    other_cols = [c for c in df_clean.columns if c not in existing_std]
    return df_clean[existing_std + other_cols]


class GoogleSheetsHandler:

    def __init__(self, credentials_file: str, allow_offline: bool = True):
        self.scope = ['https://www.googleapis.com/auth/spreadsheets']
        self.credentials_file = credentials_file
        self.creds = None
        self.client = None
        self.logger = logging.getLogger(__name__)
        self.spreadsheet_cache = {}
        self._lock = threading.Lock()
        self.online = False
        self.allow_offline = allow_offline
        self._authenticate()
        
    def _get_worksheets(self, spreadsheet):
        """Get worksheets list from cache if available or fetch fresh."""
        with self._lock:
            if not hasattr(spreadsheet, '_ws_list_cache'):
                spreadsheet._ws_list_cache = spreadsheet.worksheets()
            return spreadsheet._ws_list_cache

    def _clear_worksheet_cache(self, spreadsheet):
        """Clear worksheet list cache on mutations (add/del worksheet)."""
        with self._lock:
            if hasattr(spreadsheet, '_ws_list_cache'):
                delattr(spreadsheet, '_ws_list_cache')

    def _authenticate(self):
        """Authenticate with Google Sheets"""
        try:
            # Check if credentials file exists
            if not Path(self.credentials_file).exists():
                self.logger.error(f"Credentials file {self.credentials_file} not found!")
                raise FileNotFoundError(f"Credentials file {self.credentials_file} not found")
                
            self.creds = Credentials.from_service_account_file(
                self.credentials_file, 
                scopes=self.scope
            )
            self.client = gspread.authorize(self.creds)
            self.sheets_service = build('sheets', 'v4', credentials=self.creds)
            self.logger.info("✅ Google Sheets authentication successful")
            self.online = True
            
        except Exception as e:
            self.logger.error(f"❌ Google Sheets authentication error: {e}")
            if self.allow_offline:
                self.online = False
                self.logger.warning("⚠️ Running in offline mode due to Google Sheets authentication failure")
            else:
                raise
            
    def _is_sheet_id(self, value: str) -> bool:
        """Detect whether a value is a Google Sheets ID or URL."""
        if not value or value.strip() == '':
            return False
        if 'docs.google.com' in value:
            return True
        # Common sheet IDs are around 44 chars and use URL-safe characters
        cleaned = value.strip()
        return len(cleaned) >= 30 and len(cleaned) <= 60 and all(c.isalnum() or c in ['-', '_'] for c in cleaned)

    def _extract_sheet_id(self, value: str) -> str:
        """Extract the sheet ID from a Google Sheets URL or return the raw value."""
        if not value:
            return ''
        if 'docs.google.com' in value:
            import re
            match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
            if match:
                return match.group(1)
        return value.strip()

    def _get_error_text(self, error: Exception) -> str:
        """Normalize error text from gspread/google API exceptions."""
        if hasattr(error, 'args') and error.args:
            arg0 = error.args[0]
            if isinstance(arg0, dict):
                try:
                    return json.dumps(arg0).lower()
                except Exception:
                    return str(arg0).lower()
            return str(arg0).lower()
        if hasattr(error, 'response'):
            return str(error.response).lower()
        return str(error).lower()

    def _is_insufficient_scope_error(self, error: Exception) -> bool:
        """Detect insufficient permissions due to Drive scope restrictions."""
        message = self._get_error_text(error)
        checks = [
            'drivefiles.list',
            'access_token_scope_insufficient',
            'insufficient authentication scopes',
            'drive.googleapis.com',
            'google.apps.drive.v3',
            'insufficient permission',
            'permission denied'
        ]
        return any(check in message for check in checks)

    def _open_spreadsheet(self, sheet_name: str):
        """Open a spreadsheet by ID or title, favoring IDs when available, caching opened spreadsheets."""
        cache_key = self._extract_sheet_id(sheet_name) if self._is_sheet_id(sheet_name) else sheet_name
        with self._lock:
            if cache_key in self.spreadsheet_cache:
                return self.spreadsheet_cache[cache_key]

        if self._is_sheet_id(sheet_name):
            key = self._extract_sheet_id(sheet_name)
            try:
                sp = self.client.open_by_key(key)
                with self._lock:
                    self.spreadsheet_cache[key] = sp
                    self.spreadsheet_cache[sheet_name] = sp
                return sp
            except PermissionError as pe:
                sa_email = getattr(getattr(self.client, 'auth', None), 'service_account_email', 'your service account')
                raise PermissionError(f"Permission denied to open sheet '{key}'. Please share the Google Sheet with: {sa_email}") from pe

        try:
            sp = self.client.open(sheet_name)
            with self._lock:
                self.spreadsheet_cache[sheet_name] = sp
            return sp
        except gspread.exceptions.SpreadsheetNotFound:
            raise
        except Exception as e:
            if self._is_insufficient_scope_error(e):
                self.logger.warning("⚠️ Sheet title lookup requires Drive scope. Falling back to creating a new spreadsheet.")
                raise PermissionError("Insufficient scopes to open spreadsheet by title") from e
            raise

    def _open_or_create_spreadsheet(self, sheet_name: str):
        """Open a spreadsheet or create it if it cannot be resolved by title under Sheets-only auth."""
        try:
            return self._open_spreadsheet(sheet_name)
        except gspread.exceptions.SpreadsheetNotFound:
            self.logger.warning(f"⚠️ Spreadsheet '{sheet_name}' not found by title. Creating new spreadsheet.")
            return self._create_spreadsheet(sheet_name)
        except PermissionError as e:
            self.logger.error(
                "❌ Cannot resolve spreadsheet title with current auth scopes. "
                "Use a spreadsheet ID, share the sheet with the service account, or add Drive scope to credentials."
            )
            raise
        except Exception as e:
            if self._is_insufficient_scope_error(e):
                self.logger.error(
                    "❌ Cannot resolve spreadsheet title due to insufficient authentication scopes. "
                    "Use a spreadsheet ID or grant Drive access to the service account."
                )
                raise PermissionError("Insufficient scopes to open spreadsheet by title") from e
            raise

    def _create_spreadsheet(self, title: str):
        """Create a new spreadsheet using the Sheets API and return it."""
        body = {
            'properties': {
                'title': title
            }
        }
        try:
            response = self.sheets_service.spreadsheets().create(body=body).execute()
        except HttpError as e:
            message = self._get_error_text(e)
            self.logger.error(
                f"❌ Could not create spreadsheet '{title}': {message}. "
                "Check that the service account has permission to create sheets and that the Sheets API is enabled."
            )
            raise PermissionError("Service account cannot create spreadsheets") from e
        spreadsheet_id = response.get('spreadsheetId')
        if not spreadsheet_id:
            raise RuntimeError('Could not create spreadsheet via Sheets API')
        return self.client.open_by_key(spreadsheet_id)

    def get_master_employee(self, sheet_name: str) -> pd.DataFrame:
        """Get all master employee data from Google Sheets (NRP, Nama Staff, Posisi Update, Outlet, AM Baru)."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning empty master employee data.")
            return pd.DataFrame()

        try:
            self.logger.info(f"📊 Fetching master employee data from: {sheet_name}")
            
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = None
            try:
                for ws in spreadsheet.worksheets():
                    w_title = ws.title.lower()
                    if w_title in ['mp database', 'mp_database', 'qr attendance', 'qr_attendance', 'employee', 'employees', 'sheet1']:
                        sheet = ws
                        break
                if not sheet:
                    sheet = spreadsheet.sheet1
            except Exception:
                sheet = spreadsheet.sheet1

            self.logger.info(f"✅ Found spreadsheet: {sheet_name}, worksheet: {sheet.title}")
            
            all_values = sheet.get_all_values()
            if not all_values:
                return pd.DataFrame()

            # Find header row containing 'nrp'
            header_row_idx = -1
            headers = []
            for r_idx, r in enumerate(all_values[:5]):
                row_headers = [str(h).strip().lower() for h in r]
                if 'nrp' in row_headers:
                    header_row_idx = r_idx
                    headers = [str(h).strip() for h in r]
                    break

            if header_row_idx != -1 and len(all_values) > header_row_idx + 1:
                rows = all_values[header_row_idx + 1:]
                df = pd.DataFrame(rows, columns=headers)
            else:
                data = sheet.get_all_records()
                df = pd.DataFrame(data) if data else pd.DataFrame()

            if 'Posisi Update' in df.columns and 'Posisi' in df.columns:
                df = df.drop(columns=['Posisi'])
            elif 'Posisi' in df.columns and 'Posisi Update' not in df.columns:
                df['Posisi Update'] = df['Posisi']
                df = df.drop(columns=['Posisi'])

            self.logger.info(f"✅ Loaded {len(df)} employees from master sheet")
            return df
            
        except Exception as e:
            self.logger.error(f"❌ Error loading master employee: {e}")
            return pd.DataFrame()

    def replicate_mp_database(self, source_sheet_id: str, target_sheet_id: str) -> pd.DataFrame:
        """
        Reads MP Database from source_sheet_id, extracts (NRP, Nama Staff, Posisi Update, Outlet, AM Baru),
        overwrites tab 'MP Database' in target_sheet_id with these 5 columns, and returns the DataFrame.
        """
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot replicate MP Database.")
            return pd.DataFrame()

        try:
            self.logger.info(f"📊 Fetching source MP Database from: {source_sheet_id}")
            src_spreadsheet = self._open_or_create_spreadsheet(source_sheet_id)
            
            src_ws = None
            try:
                for ws in src_spreadsheet.worksheets():
                    w_title = ws.title.lower()
                    if w_title in ['mp database', 'mp_database', 'qr attendance', 'qr_attendance', 'employee', 'employees', 'sheet1']:
                        src_ws = ws
                        break
                if not src_ws:
                    src_ws = src_spreadsheet.sheet1
            except Exception:
                src_ws = src_spreadsheet.sheet1

            raw_data = src_ws.get_all_values()
            if not raw_data:
                self.logger.warning("Source MP Database worksheet is empty.")
                return pd.DataFrame()

            # Find header row containing 'nrp'
            header_row_idx = -1
            headers = []
            for r_idx, r in enumerate(raw_data[:5]):
                row_headers = [str(h).strip().lower() for h in r]
                if 'nrp' in row_headers:
                    header_row_idx = r_idx
                    headers = row_headers
                    break

            if header_row_idx == -1 or len(raw_data) <= header_row_idx + 1:
                self.logger.warning("Header row containing 'NRP' not found in source MP Database.")
                return pd.DataFrame()

            nrp_idx = -1
            name_idx = -1
            pos_idx = -1
            out_idx = -1
            am_idx = -1

            for idx, h in enumerate(headers):
                if h == 'nrp':
                    nrp_idx = idx
                elif h in ['nama staff', 'nama', 'name', 'employee_name']:
                    name_idx = idx
                elif h in ['posisi update', 'posisi', 'poss pivot', 'position']:
                    pos_idx = idx
                elif h in ['outlet']:
                    out_idx = idx
                elif h in ['am baru', 'area manager']:
                    am_idx = idx

            clean_rows = []
            export_rows = [["NRP", "Nama Staff", "Posisi Update", "Outlet", "AM Baru"]]

            for row in raw_data[header_row_idx + 1:]:
                nrp_val = row[nrp_idx].strip() if 0 <= nrp_idx < len(row) else ""
                name_val = row[name_idx].strip() if 0 <= name_idx < len(row) else ""
                pos_val = row[pos_idx].strip() if 0 <= pos_idx < len(row) else ""
                out_val = row[out_idx].strip() if 0 <= out_idx < len(row) else ""
                am_val = row[am_idx].strip() if 0 <= am_idx < len(row) else ""

                if not nrp_val and not name_val:
                    continue

                clean_rows.append({
                    "NRP": nrp_val,
                    "Nama Staff": name_val,
                    "Posisi Update": pos_val,
                    "Outlet": out_val,
                    "AM Baru": am_val
                })
                export_rows.append([nrp_val, name_val, pos_val, out_val, am_val])

            df_emp = pd.DataFrame(clean_rows)

            # Replicate to target sheet tab 'MP Database'
            if target_sheet_id:
                try:
                    self.logger.info(f"📤 Overwriting tab 'MP Database' in target sheet: {target_sheet_id}")
                    tgt_spreadsheet = self._open_or_create_spreadsheet(target_sheet_id)
                    try:
                        tgt_ws = tgt_spreadsheet.worksheet("MP Database")
                    except Exception:
                        tgt_ws = tgt_spreadsheet.add_worksheet(title="MP Database", rows=max(len(export_rows) + 50, 100), cols=5)

                    tgt_ws.clear()
                    tgt_ws.append_rows(export_rows)
                    self.logger.info(f"✅ Successfully replicated {len(clean_rows)} records to QR Attendance sheet tab 'MP Database'.")
                    
                    # Also sync distinct AM Baru list to 'AM Baru' tab
                    self.sync_am_baru_tab(target_sheet_id, df_emp)
                except Exception as tgt_err:
                    self.logger.error(f"⚠️ Error replicating to target QR Attendance sheet: {tgt_err}")

            return df_emp
        except Exception as e:
            self.logger.error(f"❌ Error in replicate_mp_database: {e}")
            return pd.DataFrame()

    def sync_am_baru_tab(self, target_sheet_id: str, df_emp: pd.DataFrame) -> int:
        """Extract distinct 'AM Baru' values from df_emp and sync to 'AM Baru' tab with PIN column."""
        if not self.online or not self.client or df_emp is None or df_emp.empty:
            return 0
        if 'AM Baru' not in df_emp.columns:
            return 0

        try:
            am_list = sorted(list(set(str(x).strip() for x in df_emp['AM Baru'].dropna().unique() if str(x).strip() not in ["", "nan", "None", "NaN"])))
            if not am_list:
                return 0

            spreadsheet = self._open_or_create_spreadsheet(target_sheet_id)
            existing_pins = {}
            try:
                ws = spreadsheet.worksheet("AM Baru")
                records = ws.get_all_records()
                for r in records:
                    am_name = str(r.get("AM Baru", "")).strip()
                    pin_val = str(r.get("PIN", "")).strip()
                    if am_name and pin_val:
                        existing_pins[am_name.lower()] = pin_val
            except Exception:
                ws = spreadsheet.add_worksheet(title="AM Baru", rows=max(len(am_list) + 20, 50), cols=2)

            export_rows = [["AM Baru", "PIN"]]
            for am in am_list:
                pin = existing_pins.get(am.lower(), "1234")
                export_rows.append([am, pin])

            ws.clear()
            ws.append_rows(export_rows)
            self.logger.info(f"✅ Successfully synced {len(am_list)} distinct Area Managers with PINs to 'AM Baru' tab.")
            return len(am_list)
        except Exception as e:
            self.logger.error(f"❌ Error syncing AM Baru tab: {e}")
            return 0
            
    def _get_attendance_worksheet(self, spreadsheet):
        """Get or create the 'attendance_records' worksheet from the spreadsheet."""
        try:
            return spreadsheet.worksheet('attendance_records')
        except gspread.exceptions.WorksheetNotFound:
            ws_found = None
            for ws in spreadsheet.worksheets():
                title_l = ws.title.lower()
                if 'attendance' in title_l and 'approval' not in title_l:
                    ws_found = ws
                    break
            if ws_found:
                return ws_found
            try:
                sheet = spreadsheet.add_worksheet(title="attendance_records", rows=1000, cols=15)
                headers = ['NRP', 'employee_name', 'timestamp', 'date', 'time', 
                          'type', 'timezone', 'work_start', 'work_end', 
                          'notes', 'fingerprint_id']
                sheet.append_row(headers)
                return sheet
            except Exception:
                return spreadsheet.sheet1

    def record_attendance(self, sheet_name: str, attendance_data: Dict) -> bool:
        """Record attendance to Google Sheets with better error handling"""
        if not self.online or not self.client:
            self.logger.info("Connection offline. Attempting to reconnect...")
            self.test_connection()

        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Saving attendance locally instead.")
            self._save_attendance_locally(attendance_data)
            return False

        try:
            self.logger.info(f"📝 Recording attendance to: {sheet_name}")
            self.logger.info(f"📋 Data: {attendance_data}")
            
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = self._get_attendance_worksheet(spreadsheet)
            if spreadsheet:
                self.logger.info(f"✅ Found existing spreadsheet: {sheet_name} (worksheet: {sheet.title})")
            
            headers = ['NRP', 'employee_name', 'timestamp', 'date', 'time', 
                      'type', 'timezone', 'work_start', 'work_end', 
                      'notes', 'fingerprint_id']
            if sheet.row_count <= 1:
                sheet.append_row(headers)
                self.logger.info(f"✅ Created new headers in {sheet.title}")
            
            # Prepare row data
            row = [
                attendance_data.get('NRP', ''),
                attendance_data.get('employee_name', ''),
                attendance_data.get('timestamp', datetime.now().isoformat()),
                attendance_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                attendance_data.get('time', datetime.now().strftime('%H:%M:%S')),
                attendance_data.get('type', 'CLOCK_IN'),
                attendance_data.get('timezone', 'WIB'),
                attendance_data.get('work_start', ''),
                attendance_data.get('work_end', ''),
                attendance_data.get('notes', ''),
                attendance_data.get('fingerprint_id', '')
            ]
            
            self.logger.info(f"📤 Appending row: {row}")
            result = sheet.append_row(row)
            self.logger.info(f"✅ Attendance recorded successfully! Row added: {result}")
            return True
            
        except PermissionError as e:
            self.logger.error(f"❌ Permission error recording attendance: {e}")
            self._save_attendance_locally(attendance_data)
            return False
        except gspread.exceptions.APIError as e:
            self.logger.error(f"❌ Google Sheets API error: {e}")
            self._save_attendance_locally(attendance_data)
            return False
        except Exception as e:
            self.logger.error(f"❌ Error recording attendance: {e}")
            self._save_attendance_locally(attendance_data)
            return False
            
    def get_attendance_records(self, sheet_name: str, 
                              start_date: Optional[str] = None, 
                              end_date: Optional[str] = None) -> pd.DataFrame:
        """Get attendance records from 'attendance_records' tab with optional date filter"""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning local/empty attendance records.")
            return pd.DataFrame()

        try:
            self.logger.info(f"📊 Fetching attendance records from: {sheet_name}")
            
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = self._get_attendance_worksheet(spreadsheet)
            self.logger.info(f"✅ Found attendance worksheet '{sheet.title}' in: {sheet_name}")
            
            # Get all records
            data = sheet.get_all_records()
            
            if not data:
                self.logger.info(f"ℹ️ No data in {sheet_name}")
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            self.logger.info(f"✅ Retrieved {len(df)} attendance records")

            # Parse working_hour into work_start/work_end and propagate across employee/date
            df = populate_working_hours(df)

            # Convert date column if it exists
            if 'date' in df.columns:
                try:
                    df['date'] = pd.to_datetime(df['date'], errors='coerce', format='mixed')
                    
                    # Filter by date range
                    if start_date and end_date:
                        start = pd.to_datetime(start_date)
                        end = pd.to_datetime(end_date)
                        mask = (df['date'] >= start) & (df['date'] <= end)
                        df = df[mask]
                        self.logger.info(f"✅ Filtered to {len(df)} records between {start_date} and {end_date}")
                        
                except Exception as e:
                    self.logger.warning(f"⚠️ Error parsing dates: {e}")
            
            return df
            
        except PermissionError as e:
            self.logger.error(f"❌ Permission error getting attendance records: {e}")
            return pd.DataFrame()
        except Exception as e:
            self.logger.error(f"❌ Error getting attendance records: {e}")
            return pd.DataFrame()
            
    def _save_attendance_locally(self, attendance_data: Dict):
        """Save attendance data locally if Google Sheets fails"""
        try:
            import json
            from pathlib import Path
            
            # Create data directory if it doesn't exist
            credentials_dir = Path(self.credentials_file).parent
            if credentials_dir.name == 'data':
                data_dir = credentials_dir / 'data'
            else:
                data_dir = credentials_dir / 'data' / 'data'
            data_dir.mkdir(parents=True, exist_ok=True)
            
            # Save to local JSON file
            file_path = data_dir / 'attendance_backup.json'
            
            # Load existing data
            existing_data = []
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    
            # Ensure synced flag is False by default
            rec_to_save = attendance_data.copy()
            if "synced" not in rec_to_save:
                rec_to_save["synced"] = False
                
            # Check duplicate based on NRP, timestamp, type
            exists = any(
                (str(r.get("NRP")).strip() == str(rec_to_save.get("NRP")).strip() and 
                 str(r.get("timestamp")).strip() == str(rec_to_save.get("timestamp")).strip() and 
                 str(r.get("type")).strip() == str(rec_to_save.get("type")).strip())
                for r in existing_data
            )
            if not exists:
                existing_data.append(rec_to_save)
                
            # Save back to file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, default=str)
                
            self.logger.info(f"💾 Saved attendance data locally to {file_path}")
            
        except Exception as e:
            self.logger.error(f"❌ Error saving local backup: {e}")
            
    def test_connection(self) -> bool:
        """Test Google Sheets connection using a fast TCP socket connection check."""
        import socket
        try:
            # Quick DNS and TCP connection check to Google Sheets API endpoint
            socket.setdefaulttimeout(2.0)
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("sheets.googleapis.com", 443))
            is_reachable = True
        except Exception:
            is_reachable = False

        if not is_reachable:
            self.online = False
            return False

        if not self.client:
            self._authenticate()
        else:
            self.online = True
        return self.online

    def reconnect(self) -> bool:
        """Force re-authentication to check if online status can be restored."""
        self._authenticate()
        return self.online

    def sync_local_records(self, sheet_name: str) -> tuple[int, int]:
        """
        Sync local offline backup records to Google Sheets.
        Updates 'synced' flag to True for successfully uploaded records.
        """
        from pathlib import Path
        import json
        
        credentials_dir = Path(self.credentials_file).parent
        if credentials_dir.name == 'data':
            file_path = credentials_dir / 'data' / 'attendance_backup.json'
        else:
            file_path = credentials_dir / 'data' / 'data' / 'attendance_backup.json'
            
        if not file_path.exists():
            return 0, 0
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except Exception as e:
            self.logger.error(f"❌ Error reading local backup file: {e}")
            return 0, 0
            
        if not records:
            return 0, 0
            
        # Find unsynced records
        unsynced_indices = []
        unsynced_records = []
        for idx, r in enumerate(records):
            if not r.get("synced", False):
                unsynced_indices.append(idx)
                unsynced_records.append(r)
                
        if not unsynced_records:
            return 0, 0
            
        # If offline, try to reconnect first
        if not self.online:
            self.logger.info("🔄 Trying to reconnect to Google Sheets for sync...")
            self._authenticate()
            if not self.online:
                self.logger.warning("❌ Cannot sync: Google Sheets is still offline.")
                return 0, len(unsynced_records)
                
        # Open spreadsheet and sheet once outside the loop
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = self._get_attendance_worksheet(spreadsheet)
        except Exception as e:
            self.logger.error(f"❌ Failed to open spreadsheet '{sheet_name}' for sync: {e}")
            return 0, len(unsynced_records)
            
        try:
            headers = ['NRP', 'employee_name', 'timestamp', 'date', 'time', 
                      'type', 'timezone', 'work_start', 'work_end', 
                      'notes', 'fingerprint_id']
            if sheet.row_count <= 1:
                sheet.append_row(headers)
        except Exception as e:
            self.logger.error(f"❌ Failed checking/appending headers: {e}")

        synced_count = 0
        failed_count = 0
        
        for idx, record in zip(unsynced_indices, unsynced_records):
            row = [
                record.get('NRP', ''),
                record.get('employee_name', ''),
                record.get('timestamp', ''),
                record.get('date', ''),
                record.get('time', ''),
                record.get('type', 'CLOCK_IN'),
                record.get('timezone', 'WIB'),
                record.get('work_start', ''),
                record.get('work_end', ''),
                record.get('notes', ''),
                record.get('fingerprint_id', '')
            ]
            try:
                sheet.append_row(row)
                records[idx]["synced"] = True
                synced_count += 1
                self.logger.info(f"✅ Synced record for employee {record.get('NRP')}")
            except Exception as ex:
                self.logger.error(f"❌ Failed to sync record: {ex}")
                failed_count += 1
                
        # Save records back to JSON with updated synced statuses
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, default=str)
        except Exception as e:
            self.logger.error(f"❌ Error updating local backup file after sync: {e}")
            
        return synced_count, failed_count

    def clear_attendance_records(self, sheet_name: str, keep_days: int = 60) -> bool:
        """Clear attendance records older than keep_days (keeping the header row and records from the last keep_days)."""
        if not self.online or not self.client:
            return False
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = self._get_attendance_worksheet(spreadsheet)
            
            # Fetch current values to check row count
            all_values = sheet.get_all_values()
            row_count = len(all_values)
            if row_count <= 1:
                return True
                
            headers = all_values[0]
            date_idx = -1
            for idx, h in enumerate(headers):
                if h.strip().lower() == 'date':
                    date_idx = idx
                    break
            if date_idx == -1:
                for idx, h in enumerate(headers):
                    if h.strip().lower() == 'timestamp':
                        date_idx = idx
                        break
            
            # Helper to parse dates
            def parse_date(date_val):
                if not date_val:
                    return None
                date_str = str(date_val).strip()
                if not date_str:
                    return None
                # Try common formats
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
                    try:
                        return datetime.strptime(date_str, fmt).date()
                    except ValueError:
                        continue
                # Fallback to pandas
                try:
                    return pd.to_datetime(date_str).date()
                except Exception:
                    return None

            if date_idx == -1:
                self.logger.warning("⚠️ Could not find 'date' or 'timestamp' column. Clearing all records.")
                sheet.delete_rows(2, row_count)
                self.logger.info(f"✅ Cleared {row_count - 1} rows from Google Sheet: {sheet_name}")
                return True
                
            threshold_date = (datetime.now() - timedelta(days=keep_days)).date()
            retained_rows = [headers]
            deleted_count = 0
            
            for i in range(1, len(all_values)):
                row = all_values[i]
                is_old = False
                if date_idx < len(row):
                    date_val = row[date_idx]
                    parsed_date = parse_date(date_val)
                    if parsed_date is not None and parsed_date < threshold_date:
                        is_old = True
                if is_old:
                    deleted_count += 1
                else:
                    retained_rows.append(row)
            
            if deleted_count == 0:
                self.logger.info(f"✅ No attendance records older than {keep_days} days to clear in Google Sheet: {sheet_name}")
                return True
                
            sheet.clear()
            sheet.update('A1', retained_rows)
            self.logger.info(f"✅ Cleared {deleted_count} rows older than {keep_days} days from Google Sheet: {sheet_name}. Retained {len(retained_rows)-1} rows.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error clearing old attendance records in Google Sheet: {e}")
            return False

    def get_all_worksheets_data(self, sheet_name: str) -> dict:
        """Fetch all worksheets data from a Google Sheet as a dictionary of DataFrames."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning empty worksheets data.")
            return {}

        try:
            self.logger.info(f"📊 Fetching worksheets from: {sheet_name}")
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            worksheets = spreadsheet.worksheets()
            self.logger.info(f"✅ Found spreadsheet: {sheet_name} with {len(worksheets)} worksheets")
            
            results = {}
            # Find the tabs we actually need to download
            # We need employee master tab (e.g. employee, employees, mp database)
            # and schedule/outlet tab (e.g. schedule, schedules, outlet, composition)
            for sheet in worksheets:
                title = sheet.title
                title_lower = title.lower()
                
                # Check if it matches employee database or schedules
                is_emp = title_lower in ['employee', 'employees', 'mp database', 'mp_database', 'qr attendance', 'qr_attendance', 'sheet1']
                is_sched = 'schedule' in title_lower or 'outlet' in title_lower or 'composition' in title_lower
                
                if not (is_emp or is_sched):
                    # Skip loading data to avoid API rate limits
                    continue
                    
                try:
                    self.logger.info(f"📊 Loading worksheet data: '{title}'")
                    # Special header handling for employee database tabs
                    if is_emp:
                        all_values = sheet.get_all_values()
                        if all_values:
                            header_row_idx = -1
                            raw_headers = []
                            for r_idx, r in enumerate(all_values[:5]):
                                if any(str(cell).strip().lower() == 'nrp' for cell in r):
                                    header_row_idx = r_idx
                                    raw_headers = r
                                    break
                            
                            if header_row_idx != -1 and len(all_values) > header_row_idx + 1:
                                seen = {}
                                headers = []
                                for idx, h in enumerate(raw_headers):
                                    h_clean = h.strip()
                                    if not h_clean:
                                        h_clean = f"Unnamed_{idx}"
                                    if h_clean in seen:
                                        seen[h_clean] += 1
                                        h_clean = f"{h_clean}_{seen[h_clean]}"
                                    else:
                                        seen[h_clean] = 0
                                    headers.append(h_clean)
                                    
                                rows = all_values[header_row_idx + 1:]
                                df = pd.DataFrame(rows, columns=headers)
                                # Map 'Posisi Update' to 'Posisi' for backward compatibility
                                if 'Posisi Update' in df.columns and 'Posisi' not in df.columns:
                                    df['Posisi'] = df['Posisi Update']
                                elif 'Poss Pivot' in df.columns and 'Posisi' not in df.columns:
                                    df['Posisi'] = df['Poss Pivot']
                            else:
                                data = sheet.get_all_records()
                                df = pd.DataFrame(data) if data else pd.DataFrame()
                        else:
                            df = pd.DataFrame()
                    else:
                        data = sheet.get_all_records()
                        if data:
                            df = pd.DataFrame(data)
                        else:
                            df = pd.DataFrame()
                            
                    results[title] = df
                    if is_emp:
                        # Also add an alias key 'employees' so the caller matches it automatically!
                        results['employees'] = df
                        results['employee'] = df
                        
                    self.logger.info(f"✅ Loaded worksheet '{title}' with {len(df)} records")
                except Exception as ws_err:
                    self.logger.error(f"Error loading worksheet '{title}': {ws_err}")
                    results[title] = pd.DataFrame()
            return results
        except Exception as e:
            self.logger.error(f"❌ Error loading worksheets: {e}")
            return {}

    def get_face_embedding_mappings(self, sheet_name: str) -> dict:
        """Fetch face embedding mappings from the 'Face_Embedding' tab on Google Sheets. Deletes old 'Fingerprint_Mapping' tab if present."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot load face embedding mappings.")
            return {}

        try:
            self.logger.info(f"📊 Accessing spreadsheet: {sheet_name} to load face embedding mappings...")
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            
            ws_titles = [w.title for w in self._get_worksheets(spreadsheet)]
            if "Fingerprint_Mapping" in ws_titles:
                try:
                    fp_ws = spreadsheet.worksheet("Fingerprint_Mapping")
                    spreadsheet.del_worksheet(fp_ws)
                    self._clear_worksheet_cache(spreadsheet)
                    self.logger.info("🗑️ Removed old 'Fingerprint_Mapping' tab from Google Sheet.")
                except Exception:
                    pass

            # Try to get or create 'Face_Embedding' worksheet
            try:
                sheet = spreadsheet.worksheet("Face_Embedding")
            except gspread.exceptions.WorksheetNotFound:
                self.logger.info("ℹ️ Worksheet 'Face_Embedding' not found. Creating it.")
                sheet = spreadsheet.add_worksheet(title="Face_Embedding", rows=1000, cols=4)
                sheet.append_row(["NRP", "Face Embedding", "Device_ID", "Updated At"])
                
            records = sheet.get_all_records()
            mappings = {}
            for r in records:
                nrp = str(r.get("NRP", "") or r.get("nrp", "")).strip()
                embedding = str(r.get("Face Embedding", "") or r.get("face_embedding", "") or r.get("Face ID", "") or "CONNECTED").strip()
                if nrp:
                    key = embedding if embedding and embedding != "CONNECTED" else nrp
                    mappings[key] = nrp
            self.logger.info(f"✅ Loaded {len(mappings)} face embedding mappings from Google Sheets.")
            return mappings
        except Exception as e:
            self.logger.error(f"❌ Error loading face embedding mappings from Google Sheet: {e}")
            return {}

    def get_raw_face_embeddings(self, sheet_name: str) -> dict:
        """Fetch all raw face embedding vectors per NRP from the 'Face_Embedding' tab on Google Sheets.
        Returns dict: { nrp: [float_list_of_128_dims] }
        """
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot fetch raw face embeddings.")
            return {}

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet("Face_Embedding")
            except gspread.exceptions.WorksheetNotFound:
                return {}

            records = sheet.get_all_records()
            nrp_embeddings = {}
            for r in records:
                nrp = str(r.get("NRP", "") or r.get("nrp", "")).strip()
                emb_raw = str(r.get("Face Embedding", "") or r.get("face_embedding", "") or r.get("Embedding", "") or r.get("Face ID", "") or "").strip()
                if nrp and emb_raw and emb_raw.startswith("["):
                    try:
                        vec = json.loads(emb_raw)
                        if isinstance(vec, list) and len(vec) >= 10:
                            nrp_embeddings[nrp] = [float(x) for x in vec]
                    except Exception:
                        pass
            self.logger.info(f"✅ Loaded {len(nrp_embeddings)} raw face embedding vectors from Google Sheets.")
            return nrp_embeddings
        except Exception as e:
            self.logger.error(f"❌ Error reading raw face embeddings from Google Sheet: {e}")
            return {}

    def save_face_embedding_mappings(self, sheet_name: str, mappings: dict, device_id_map: dict = None) -> bool:
        """Clear and overwrite the 'Face_Embedding' tab on Google Sheets with new mappings, preserving Device_ID. Deletes old 'Fingerprint_Mapping' tab if present."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot save face embedding mappings.")
            return False

        try:
            self.logger.info(f"📤 Saving {len(mappings)} face embedding mappings to spreadsheet: {sheet_name}")
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            
            ws_titles = [w.title for w in self._get_worksheets(spreadsheet)]
            if "Fingerprint_Mapping" in ws_titles:
                try:
                    fp_ws = spreadsheet.worksheet("Fingerprint_Mapping")
                    spreadsheet.del_worksheet(fp_ws)
                    self._clear_worksheet_cache(spreadsheet)
                    self.logger.info("🗑️ Removed old 'Fingerprint_Mapping' tab from Google Sheet.")
                except Exception:
                    pass

            # Fetch existing device_id mappings before clearing if not provided
            if device_id_map is None:
                device_id_map = self.get_device_id_mappings(sheet_name)

            try:
                sheet = spreadsheet.worksheet("Face_Embedding")
            except gspread.exceptions.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(title="Face_Embedding", rows=1000, cols=4)
                
            # Clear existing data
            sheet.clear()
            
            # Write headers and rows with Device_ID
            rows = [["NRP", "Face Embedding", "Device_ID", "Updated At"]]
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for key, nrp in mappings.items():
                nrp_str = str(nrp).strip()
                key_str = str(key).strip()
                emb_val = key_str if key_str != nrp_str else "CONNECTED"
                dev_id_val = str(device_id_map.get(nrp_str, "")).strip() if device_id_map else ""
                rows.append([nrp_str, emb_val, dev_id_val, now_str])
                
            sheet.append_rows(rows)
            self.logger.info("✅ Face_Embedding mappings updated successfully on Google Sheets.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error saving face embedding mappings to Google Sheet: {e}")
            return False

    def get_fingerprint_mappings(self, sheet_name: str) -> dict:
        """Alias for get_face_embedding_mappings."""
        return self.get_face_embedding_mappings(sheet_name)

    def get_device_id_mappings(self, sheet_name: str) -> dict:
        """Fetch NRP -> Device_ID mappings from 'Face_Embedding' tab in Google Sheets."""
        if not self.online or not self.client:
            return {}
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet("Face_Embedding")
            except Exception:
                return {}
            records = sheet.get_all_records()
            dev_map = {}
            for r in records:
                nrp = str(r.get("NRP", "") or r.get("nrp", "")).strip()
                dev_id = str(r.get("Device_ID", "") or r.get("Device ID", "") or r.get("device_id", "") or r.get("deviceid", "")).strip()
                if nrp:
                    dev_map[nrp] = dev_id
            return dev_map
        except Exception as e:
            self.logger.error(f"❌ Error loading device_id mappings from Google Sheet: {e}")
            return {}

    def save_fingerprint_mappings(self, sheet_name: str, mappings: dict, device_id_map: dict = None) -> bool:
        """Alias for save_face_embedding_mappings."""
        return self.save_face_embedding_mappings(sheet_name, mappings, device_id_map)

    def get_svp_approval_records(self, sheet_name: str) -> pd.DataFrame:
        """Fetch all supervisor approval records from the 'SVP Approval' worksheet in Google Sheets."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning empty SVP approvals.")
            return pd.DataFrame()

        try:
            self.logger.info(f"📊 Fetching SVP approvals from: {sheet_name}")
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            
            try:
                sheet = spreadsheet.worksheet('SVP Approval')
            except gspread.exceptions.WorksheetNotFound:
                self.logger.warning("⚠️ Worksheet 'SVP Approval' not found in spreadsheet. Returning empty DataFrame.")
                return pd.DataFrame()

            data = sheet.get_all_records()
            if not data:
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            self.logger.info(f"✅ Retrieved {len(df)} SVP approval records")
            return df
        except Exception as e:
            self.logger.error(f"❌ Error getting SVP approval records: {e}")
            return pd.DataFrame()

    def clear_svp_approval_records(self, sheet_name: str) -> bool:
        """Clear all records from 'SVP Approval' worksheet in Google Sheets (keeping only the header row)."""
        if not self.online or not self.client:
            return False
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = spreadsheet.worksheet('SVP Approval')
            
            all_values = sheet.get_all_values()
            row_count = len(all_values)
            if row_count > 1:
                sheet.delete_rows(2, row_count)
                self.logger.info(f"✅ Cleared {row_count - 1} rows from Google Sheet 'SVP Approval' tab.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error clearing 'SVP Approval' records in Google Sheet: {e}")
            return False

    def get_unbind_device_requests(self, sheet_name: str) -> pd.DataFrame:
        """Fetch pending device unbind requests from the 'Device_Unbind_Requests' worksheet in Google Sheets."""
        if not self.online or not self.client:
            return pd.DataFrame()

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Device_Unbind_Requests')
            except gspread.exceptions.WorksheetNotFound:
                return pd.DataFrame()

            data = sheet.get_all_records()
            if not data:
                return pd.DataFrame()
                
            df = pd.DataFrame(data)
            if 'Status' in df.columns:
                df = df[df['Status'].astype(str).str.upper().str.strip() == 'PENDING']
            return df
        except Exception as e:
            self.logger.error(f"❌ Error getting unbind device requests: {e}")
            return pd.DataFrame()

    def process_unbind_device_request(self, sheet_name: str, nrp: str, action_status: str = "APPROVED") -> bool:
        """Process unbind device request: clear Device_ID in Face_Embedding tab and update status in Device_Unbind_Requests."""
        if not self.online or not self.client:
            return False

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            nrp_clean = str(nrp).strip().lower()

            if action_status.upper() == "APPROVED":
                # Delete row in Face_Embedding worksheet for this NRP
                try:
                    face_sheet = spreadsheet.worksheet("Face_Embedding")
                    all_vals = face_sheet.get_all_values()
                    if len(all_vals) > 1:
                        rows_to_delete = []
                        for r_idx in range(1, len(all_vals)):
                            row = all_vals[r_idx]
                            if row and len(row) > 0 and str(row[0]).strip().lower() == nrp_clean:
                                rows_to_delete.append(r_idx + 1)
                        for r_num in reversed(rows_to_delete):
                            face_sheet.delete_rows(r_num)
                        self._clear_worksheet_cache(spreadsheet)
                        self.logger.info(f"✅ Deleted record for NRP {nrp} from Face_Embedding tab.")
                except Exception as fe_err:
                    self.logger.error(f"Error deleting Face_Embedding record for NRP {nrp}: {fe_err}")

            # Update status in Device_Unbind_Requests worksheet
            try:
                unbind_sheet = spreadsheet.worksheet("Device_Unbind_Requests")
                all_un = unbind_sheet.get_all_values()
                if len(all_un) > 1:
                    headers = [str(h).lower().strip() for h in all_un[0]]
                    status_col_idx = 4
                    for idx, h in enumerate(headers):
                        if h == 'status':
                            status_col_idx = idx
                            break
                    for r_idx in range(1, len(all_un)):
                        row = all_un[r_idx]
                        if row and len(row) > 0 and str(row[0]).strip().lower() == nrp_clean:
                            unbind_sheet.update_cell(r_idx + 1, status_col_idx + 1, action_status.upper())
                            self.logger.info(f"✅ Updated unbind request status to {action_status} for NRP {nrp}.")
            except Exception as un_err:
                self.logger.error(f"Error updating status in Device_Unbind_Requests: {un_err}")

            return True
        except Exception as e:
            self.logger.error(f"❌ Error processing unbind device request: {e}")
            return False

    def cleanup_resigned_face_embeddings(self, sheet_name: str, valid_nrps: set) -> int:
        """Remove records from 'Face_Embedding' worksheet whose NRP is no longer in valid_nrps (resigned employees)."""
        if not self.online or not self.client or not valid_nrps:
            return 0

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                face_sheet = spreadsheet.worksheet("Face_Embedding")
            except Exception:
                return 0

            all_vals = face_sheet.get_all_values()
            if len(all_vals) <= 1:
                return 0

            valid_set = set(str(n).replace("'", "").replace('"', "").strip().lower() for n in valid_nrps if str(n).strip())
            
            rows_to_delete = []
            for r_idx in range(1, len(all_vals)):
                row = all_vals[r_idx]
                if row and len(row) > 0:
                    row_nrp = str(row[0]).replace("'", "").replace('"', "").strip().lower()
                    if row_nrp and row_nrp not in valid_set:
                        rows_to_delete.append(r_idx + 1)

            if rows_to_delete:
                for r_num in reversed(rows_to_delete):
                    face_sheet.delete_rows(r_num)
                self._clear_worksheet_cache(spreadsheet)
                self.logger.info(f"🗑️ Removed {len(rows_to_delete)} resigned face embedding records from Google Sheets 'Face_Embedding' tab.")

            return len(rows_to_delete)
        except Exception as e:
            self.logger.error(f"❌ Error cleaning up resigned face embeddings: {e}")
            return 0

    def get_device_bind_history(self, sheet_name: str) -> pd.DataFrame:
        """Fetch all device bind/unbind history from the 'Device_Bind_History' worksheet.
        
        Returns a DataFrame with columns:
            Timestamp, NRP, Device_ID, Action, Triggered_By, Notes
        """
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets offline — returning empty Device_Bind_History.")
            return pd.DataFrame()
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Device_Bind_History')
            except Exception:
                return pd.DataFrame()

            data = sheet.get_all_records()
            if not data:
                return pd.DataFrame()

            df = pd.DataFrame(data)
            # Normalize column names
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception as e:
            self.logger.error(f"❌ Error fetching Device_Bind_History: {e}")
            return pd.DataFrame()

    def get_outlet_schedules(self, sheet_name: str) -> pd.DataFrame:
        """Fetch outlet shift schedules from 'Outlet Schedule' (or 'Schedule') worksheet in Google Sheets."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning empty Outlet Schedule DataFrame.")
            return pd.DataFrame()

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = None
            try:
                for ws in spreadsheet.worksheets():
                    if 'schedule' in ws.title.lower():
                        sheet = ws
                        break
            except Exception:
                sheet = None

            if not sheet:
                return pd.DataFrame()

            data = sheet.get_all_records()
            if not data:
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except Exception as e:
            self.logger.error(f"❌ Error loading outlet schedules from Google Sheet: {e}")
            return pd.DataFrame()

    def generate_base32_secret(self, length: int = 16) -> str:
        """Generate a 16-character Base32 secret string (A-Z, 2-7)."""
        import secrets
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def get_outlets_data(self, sheet_name: str) -> pd.DataFrame:
        """Fetch all outlet records from the 'Outlets' worksheet in Google Sheets. Creates worksheet if missing."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is unavailable. Returning empty Outlets DataFrame.")
            return pd.DataFrame()

        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Outlets')
            except gspread.exceptions.WorksheetNotFound:
                try:
                    sheet = spreadsheet.worksheet('Outlet')
                except gspread.exceptions.WorksheetNotFound:
                    self.logger.info("ℹ️ Worksheet 'Outlets' not found. Creating it with default headers.")
                    sheet = spreadsheet.add_worksheet(title='Outlets', rows=1000, cols=6)
                    sheet.append_row(['Outlet', 'Latitude', 'Longitude', 'Radius', 'Secret', 'HK'])

            data = sheet.get_all_records()
            if not data:
                raw_values = sheet.get_all_values()
                if not raw_values:
                    sheet.append_row(['Outlet', 'Latitude', 'Longitude', 'Radius', 'Secret', 'HK'])
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df = deduplicate_outlets_df(df)

            self.logger.info(f"✅ Loaded {len(df)} records from 'Outlets' worksheet.")
            return df
        except Exception as e:
            self.logger.error(f"❌ Error getting outlets data: {e}")
            return pd.DataFrame()

    def sync_outlets_with_mp_database(self, sheet_name: str, mp_outlet_list: List[str]) -> pd.DataFrame:
        """
        Syncs outlets from MP Database with the Outlets worksheet in Google Sheets:
        - Extracts distinct outlets from mp_outlet_list.
        - Adds newly registered outlets (with Radius = 50 and auto-generated Base32 Secret).
        - Prunes/removes outlets from Outlets tab if they are no longer in MP Database.
        - Keeps existing Secret keys, Latitude, Longitude, and Radius for outlets still active.
        - Updates the Outlets worksheet in Google Sheets and returns the complete DataFrame.
        """
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Skipping Google Sheets outlets sync.")
            return pd.DataFrame()

        try:
            # 1. Fetch current outlets tab (already deduplicated and cleaned)
            df_existing = self.get_outlets_data(sheet_name)

            # Standard columns
            std_cols = ['Outlet', 'Latitude', 'Longitude', 'Radius', 'Secret', 'HK', 'pwa_url']

            if df_existing.empty:
                df_existing = pd.DataFrame(columns=std_cols)
            else:
                for col in std_cols:
                    if col not in df_existing.columns:
                        df_existing[col] = ''

            # Clean and filter MP Database outlet list
            clean_mp_outlets = []
            mp_names_set = set()
            for o in mp_outlet_list:
                o_clean = str(o).strip()
                if o_clean and o_clean.lower() not in mp_names_set and o_clean.lower() not in ['nan', 'none', 'n/a']:
                    mp_names_set.add(o_clean.lower())
                    clean_mp_outlets.append(o_clean)

            # 2. Prune existing outlets that are no longer in MP Database
            if not df_existing.empty and 'Outlet' in df_existing.columns and mp_names_set:
                df_existing = df_existing[df_existing['Outlet'].astype(str).str.strip().str.lower().isin(mp_names_set)].copy()

            # Existing active outlet names
            existing_names_set = set()
            if 'Outlet' in df_existing.columns:
                for _, r in df_existing.iterrows():
                    val = str(r.get('Outlet', '')).strip()
                    if val and val.lower() not in ['nan', 'none', 'n/a']:
                        existing_names_set.add(val.lower())

            # 3. Identify and add new outlets from MP Database
            default_pwa_url = 'https://goldenlamian.dolanyu.com/'
            new_rows = []
            for o_name in clean_mp_outlets:
                if o_name.lower() not in existing_names_set:
                    new_secret = self.generate_base32_secret(16)
                    row_dict = {col: '' for col in std_cols}
                    row_dict['Outlet'] = o_name
                    row_dict['Radius'] = 50
                    row_dict['Secret'] = new_secret
                    row_dict['HK'] = 22
                    row_dict['pwa_url'] = default_pwa_url
                    new_rows.append(row_dict)
                    existing_names_set.add(o_name.lower())

            if new_rows:
                df_new = pd.DataFrame(new_rows)
                df_updated = pd.concat([df_existing, df_new], ignore_index=True)
                self.logger.info(f"✨ Found {len(new_rows)} new outlets in MP Database. Added to Outlets tab.")
            else:
                df_updated = df_existing.copy()

            # Ensure all outlets have a secret key, radius, default HK (22), and default pwa_url if empty
            secret_col = 'Secret'
            radius_col = 'Radius'
            hk_col = 'HK'
            pwa_col = 'pwa_url'
            for idx, row in df_updated.iterrows():
                sec_val = str(row.get(secret_col, '')).strip()
                if not sec_val or sec_val.lower() in ['nan', 'none']:
                    df_updated.at[idx, secret_col] = self.generate_base32_secret(16)
                rad_val = str(row.get(radius_col, '')).strip()
                if not rad_val or rad_val.lower() in ['nan', 'none']:
                    df_updated.at[idx, radius_col] = 50
                hk_val = str(row.get(hk_col, '')).strip()
                if not hk_val or hk_val.lower() in ['nan', 'none', '']:
                    df_updated.at[idx, hk_col] = 22
                pwa_val = str(row.get(pwa_col, '')).strip()
                if not pwa_val or pwa_val.lower() in ['nan', 'none', '']:
                    df_updated.at[idx, pwa_col] = default_pwa_url

            # Final deduplication and standard column order
            df_updated = deduplicate_outlets_df(df_updated)

            # Update Google Sheet
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Outlets')
            except gspread.exceptions.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(title='Outlets', rows=max(1000, len(df_updated) + 50), cols=max(5, len(df_updated.columns)))

            sheet.clear()
            
            # Format headers and data
            headers = list(df_updated.columns)
            rows_data = [headers]
            for _, r in df_updated.fillna('').iterrows():
                rows_data.append([str(r[c]) for c in headers])

            sheet.append_rows(rows_data)
            self.logger.info(f"✅ Successfully synced {len(df_updated)} active outlets in Google Sheet tab 'Outlets'.")

            return df_updated

        except Exception as e:
            self.logger.error(f"❌ Error syncing outlets with MP Database: {e}")
            return pd.DataFrame()

    def save_outlets_data(self, sheet_name: str, df: pd.DataFrame) -> bool:
        """Clear and overwrite the 'Outlets' worksheet in Google Sheets with updated DataFrame."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot save outlets data.")
            return False

        try:
            if df is None or df.empty:
                return False

            df_clean = deduplicate_outlets_df(df)
            std_cols = ['Outlet', 'Latitude', 'Longitude', 'Radius', 'Secret', 'HK']
            for c in std_cols:
                if c not in df_clean.columns:
                    df_clean[c] = 22 if c == 'HK' else ''
            # Ensure empty/NaN HK is defaulted to 22
            df_clean['HK'] = df_clean['HK'].fillna(22).replace(['', 'None', 'nan', '<NA>'], 22)
            existing_std = [c for c in std_cols if c in df_clean.columns]
            other_cols = [c for c in df_clean.columns if c not in existing_std]
            df_clean = df_clean[existing_std + other_cols]

            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Outlets')
            except gspread.exceptions.WorksheetNotFound:
                try:
                    sheet = spreadsheet.worksheet('Outlet')
                except gspread.exceptions.WorksheetNotFound:
                    sheet = spreadsheet.add_worksheet(title='Outlets', rows=max(1000, len(df_clean) + 50), cols=max(5, len(df_clean.columns)))

            sheet.clear()

            headers = list(df_clean.columns)
            rows_data = [headers]
            for _, r in df_clean.fillna('').iterrows():
                rows_data.append([str(r[c]) for c in headers])

            sheet.append_rows(rows_data)
            self.logger.info(f"✅ Successfully saved and updated {len(df_clean)} outlets in Google Sheet tab 'Outlets'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error saving outlets data to Google Sheet: {e}")
            return False

    def save_outlet_schedules_data(self, sheet_name: str, df: pd.DataFrame) -> bool:
        """Clear and overwrite the 'Outlet Schedule' worksheet in Google Sheets with updated DataFrame."""
        if not self.online or not self.client:
            self.logger.warning("⚠️ Google Sheets is offline. Cannot save outlet schedules data.")
            return False

        try:
            if df is None:
                df = pd.DataFrame(columns=['Outlet', 'Working Hour', 'Shift'])

            std_cols = ['Outlet', 'Working Hour', 'Shift']
            df_clean = df.copy()
            for col in std_cols:
                if col not in df_clean.columns:
                    df_clean[col] = ''

            df_clean = df_clean[std_cols]

            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            sheet = None
            try:
                for ws in spreadsheet.worksheets():
                    if 'schedule' in ws.title.lower():
                        sheet = ws
                        break
            except Exception:
                sheet = None

            if not sheet:
                sheet = spreadsheet.add_worksheet(title='Outlet Schedule', rows=max(1000, len(df_clean) + 50), cols=4)

            sheet.clear()

            headers = list(df_clean.columns)
            rows_data = [headers]
            for _, r in df_clean.fillna('').iterrows():
                rows_data.append([str(r[c]) for c in headers])

            sheet.append_rows(rows_data)
            self.logger.info(f"✅ Successfully saved and updated {len(df_clean)} records in Google Sheet tab 'Outlet Schedule'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error saving outlet schedules data to Google Sheet: {e}")
            return False

    def get_shift_templates_data(self, sheet_name: str) -> pd.DataFrame:
        """Fetch shift templates from 'Shift_Templates' tab in Google Sheets."""
        if not self.online or not self.client:
            return pd.DataFrame()
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Shift_Templates')
            except Exception:
                return pd.DataFrame()
            data = sheet.get_all_records()
            return pd.DataFrame(data) if data else pd.DataFrame()
        except Exception as e:
            self.logger.error(f"❌ Error getting shift templates: {e}")
            return pd.DataFrame()

    def save_shift_templates_data(self, sheet_name: str, df: pd.DataFrame) -> bool:
        """Clear and overwrite the 'Shift_Templates' tab in Google Sheets."""
        if not self.online or not self.client:
            return False
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Shift_Templates')
            except Exception:
                sheet = spreadsheet.add_worksheet(title='Shift_Templates', rows=max(500, len(df)+50), cols=3)

            sheet.clear()
            headers = ['Template Name', 'Shift', 'Working Hour']
            rows_data = [headers]
            if df is not None and not df.empty:
                for _, r in df.fillna('').iterrows():
                    rows_data.append([str(r.get(c, '')) for c in headers])
            sheet.append_rows(rows_data)
            return True
        except Exception as e:
            self.logger.error(f"❌ Error saving shift templates: {e}")
            return False

    def get_outlet_template_assignments_data(self, sheet_name: str) -> pd.DataFrame:
        """Fetch outlet-to-template assignments from 'Outlet_Templates' tab in Google Sheets."""
        if not self.online or not self.client:
            return pd.DataFrame()
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Outlet_Templates')
            except Exception:
                return pd.DataFrame()
            data = sheet.get_all_records()
            return pd.DataFrame(data) if data else pd.DataFrame()
        except Exception as e:
            self.logger.error(f"❌ Error getting outlet template assignments: {e}")
            return pd.DataFrame()

    def save_outlet_template_assignments_data(self, sheet_name: str, df: pd.DataFrame) -> bool:
        """Clear and overwrite the 'Outlet_Templates' tab in Google Sheets."""
        if not self.online or not self.client:
            return False
        try:
            spreadsheet = self._open_or_create_spreadsheet(sheet_name)
            try:
                sheet = spreadsheet.worksheet('Outlet_Templates')
            except Exception:
                sheet = spreadsheet.add_worksheet(title='Outlet_Templates', rows=max(500, len(df)+50), cols=2)

            sheet.clear()
            headers = ['Outlet', 'Template Name']
            rows_data = [headers]
            if df is not None and not df.empty:
                for _, r in df.fillna('').iterrows():
                    rows_data.append([str(r.get(c, '')) for c in headers])
            sheet.append_rows(rows_data)
            return True
        except Exception as e:
            self.logger.error(f"❌ Error saving outlet template assignments: {e}")
            return False





