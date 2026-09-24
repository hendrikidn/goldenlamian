import os
import logging
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, String, Integer, text, Sequence
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

DEFAULT_HASH = "ab38eadaeb746599f2c1ee90f8267f31f467347462764a24d71ac1843ee77fe3"  # SHA-256 hash for default password

Base = declarative_base()

class AttendanceRecord(Base):
    __tablename__ = 'attendance_records'
    
    id = Column(Integer, Sequence('attendance_records_id_seq'), primary_key=True)
    NRP = Column(String, index=True, nullable=False)
    employee_name = Column(String, nullable=True)
    timestamp = Column(String, nullable=False, index=True) # Check NRP + timestamp for uniqueness
    date = Column(String, index=True, nullable=True)
    time = Column(String, nullable=True)
    type = Column(String, nullable=True)
    status = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    outlet = Column(String, nullable=True)
    distance_meters = Column(String, nullable=True)
    gps_accuracy = Column(String, nullable=True)
    work_start = Column(String, nullable=True)
    work_end = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    fingerprint_id = Column(String, nullable=True)

class AdminLog(Base):
    __tablename__ = 'admin_logs'
    
    id = Column(Integer, Sequence('admin_logs_id_seq'), primary_key=True)
    timestamp = Column(String, nullable=False)
    action = Column(String, nullable=False)
    details = Column(String, nullable=True)

class AdminCredential(Base):
    __tablename__ = 'admin_credentials'
    
    username = Column(String, primary_key=True)
    password_hash = Column(String, nullable=False)

class FingerprintMapping(Base):
    __tablename__ = 'fingerprint_mappings'
    
    fingerprint_id = Column(String, primary_key=True)
    NRP = Column(String, nullable=False)

class SvpApproval(Base):
    __tablename__ = 'svp_approvals'
    
    NRP = Column(String, primary_key=True)
    timestamp = Column(String, primary_key=True)
    svp_nrp = Column(String, nullable=True)
    svp_name = Column(String, nullable=True)
    review_status = Column(String, nullable=True)
    review_date = Column(String, nullable=True)

class DeviceBindHistory(Base):
    """Audit trail for all device bind/unbind events — used for fraud detection."""
    __tablename__ = 'device_bind_history'

    id = Column(Integer, Sequence('device_bind_history_id_seq'), primary_key=True)
    timestamp  = Column(String, nullable=False, index=True)
    NRP        = Column(String, nullable=False, index=True)
    device_id  = Column(String, nullable=True,  index=True)
    action     = Column(String, nullable=False)   # BIND | UNBIND | UNBIND_REQUEST
    triggered_by = Column(String, nullable=True)  # EMPLOYEE_SELF | HR_ADMIN | SYSTEM
    notes      = Column(String, nullable=True)


import re

def parse_case_category(notes_val: str) -> str:
    """Parse attendance notes into Case Category."""
    if not notes_val or pd.isna(notes_val):
        return ""
    n_str = str(notes_val).strip()
    n_lower = n_str.lower()
    
    # Match any bracketed tag: [Supervisor Approval Required | Case] or [APPROVED by SPV ... | Case] or [REJECTED by SPV ... | Case]
    m = re.search(r'\[(?:supervisor approval required|approved by spv|rejected by spv)[^|]*\|\s*([^\]]+)\]', n_str, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    if "diluar radius" in n_lower:
        return "Diluar Radius Outlet"
    if "gps nonaktif" in n_lower:
        return "GPS Nonaktif"
    if "unbind" in n_lower:
        return "Permintaan Unbind Device"

    return ""

def parse_svp_details_from_notes(row, df_employees=None):
    """
    Extract (SVP NRP, SVP Name, Review Status, Review Date) from notes column.
    """
    notes = str(row.get('notes', '') or '').strip()
    row_date = str(row.get('date', '') or '').strip()
    
    res_status = str(row.get('Review Status', '') or '').strip()
    res_nrp = str(row.get('SVP NRP', '') or '').strip()
    res_name = str(row.get('SVP Name', '') or '').strip()
    res_date = str(row.get('Review Date', '') or '').strip()

    # Normalize empty values
    if res_status in ['None', 'NaN', 'nan', '']: res_status = None
    if res_nrp in ['None', 'NaN', 'nan', '']: res_nrp = None
    if res_name in ['None', 'NaN', 'nan', '']: res_name = None
    if res_date in ['None', 'NaN', 'nan', '']: res_date = None

    # Check APPROVED pattern: [APPROVED by SPV <Name> (<NRP>) at <Time> | ...] or [APPROVED by SPV <Name> at <Time> | ...]
    app_match = re.search(r'\[APPROVED by SPV\s+([^(|]+?)(?:\s*\(([^)]+)\))?\s+at\s+([0-9:]{4,8})', notes, re.IGNORECASE)
    # Check REJECTED pattern
    rej_match = re.search(r'\[REJECTED by SPV\s+([^(|]+?)(?:\s*\(([^)]+)\))?\s+at\s+([0-9:]{4,8})', notes, re.IGNORECASE)

    if app_match:
        res_status = 'Approved'
        spv_name_found = app_match.group(1).strip()
        spv_nrp_found = app_match.group(2).strip() if app_match.group(2) else None
        time_found = app_match.group(3).strip()

        if spv_name_found: res_name = spv_name_found
        if spv_nrp_found: res_nrp = spv_nrp_found
        if time_found: res_date = f"{row_date} {time_found}".strip() if row_date else time_found

    elif rej_match:
        res_status = 'Rejected'
        spv_name_found = rej_match.group(1).strip()
        spv_nrp_found = rej_match.group(2).strip() if rej_match.group(2) else None
        time_found = rej_match.group(3).strip()

        if spv_name_found: res_name = spv_name_found
        if spv_nrp_found: res_nrp = spv_nrp_found
        if time_found: res_date = f"{row_date} {time_found}".strip() if row_date else time_found

    elif 'supervisor approval required' in notes.lower() or 'approval required' in notes.lower():
        res_status = 'Waiting for Supervisor approval'

    # Fallback to lookup SVP NRP by SVP Name if missing
    if res_name and not res_nrp and df_employees is not None and not df_employees.empty:
        try:
            match = df_employees[df_employees['Nama Staff'].astype(str).str.strip().str.lower() == res_name.lower()]
            if not match.empty:
                res_nrp = str(match.iloc[0]['NRP']).strip()
        except Exception:
            pass

    return pd.Series({
        'SVP NRP': res_nrp if res_nrp else "-",
        'SVP Name': res_name if res_name else "-",
        'Review Status': res_status if res_status else "-",
        'Review Date': res_date if res_date else "-"
    })

class DatabaseHandler:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Ensure directories exist
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        from sqlalchemy.pool import NullPool
        abs_path = os.path.abspath(self.db_path).replace('\\', '/')
        self.engine = create_engine(f"duckdb:///{abs_path}", echo=False, poolclass=NullPool)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.Session = sessionmaker(bind=self.engine)
        
    def _migrate_schema(self):
        """Add missing columns to attendance_records table if table already exists from previous schema."""
        try:
            with self.engine.begin() as conn:
                cols_to_add = [
                    ('outlet', 'VARCHAR'),
                    ('distance_meters', 'VARCHAR'),
                    ('gps_accuracy', 'VARCHAR'),
                    ('work_start', 'VARCHAR'),
                    ('work_end', 'VARCHAR')
                ]
                for col_name, col_type in cols_to_add:
                    try:
                        conn.execute(text(f"ALTER TABLE attendance_records ADD COLUMN {col_name} {col_type}"))
                        logger.info(f"✅ Migrated database schema: Added column {col_name} to attendance_records")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Schema migration note: {e}")
        
    def save_employees(self, df: pd.DataFrame):
        """Save or update employee records from a pandas DataFrame, saving all columns."""
        if df.empty:
            return
        try:
            # Clean and deduplicate NRP to avoid constraint violations
            df_clean = df.copy()
            if 'NRP' in df_clean.columns:
                df_clean['NRP'] = df_clean['NRP'].astype(str).str.strip()
                df_clean = df_clean[~df_clean['NRP'].isin(['', 'None', 'nan', '<NA>', 'N/A'])]
                df_clean = df_clean.drop_duplicates(subset=['NRP'], keep='first')
                
            # Use begin() to wrap both table replacement and index creation in a single transaction
            with self.engine.begin() as conn:
                df_clean.to_sql('employees', conn, if_exists='replace', index=False)
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_nrp ON employees(NRP)"))
                
            logger.info(f"Successfully saved {len(df_clean)} employees to local database with all columns.")
        except Exception as e:
            logger.error(f"Error saving employees to database: {e}")
            
    def get_employees(self) -> pd.DataFrame:
        """Fetch all employees from the database as a pandas DataFrame."""
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('employees'):
                    return pd.DataFrame()
                return pd.read_sql("SELECT * FROM employees", conn)
        except Exception as e:
            logger.error(f"Error reading employees from database: {e}")
            return pd.DataFrame()

    def save_outlet_schedules(self, df: pd.DataFrame):
        """Save or update outlet schedules from a pandas DataFrame."""
        if df.empty:
            return
        try:
            with self.engine.begin() as conn:
                df.to_sql('outlet_schedules', conn, if_exists='replace', index=False)
            logger.info(f"Successfully saved {len(df)} outlet schedules to local database.")
        except Exception as e:
            logger.error(f"Error saving outlet schedules: {e}")

    def get_outlet_schedules(self) -> pd.DataFrame:
        """Fetch all outlet schedules from the database as a pandas DataFrame."""
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('outlet_schedules'):
                    return pd.DataFrame()
                return pd.read_sql("SELECT * FROM outlet_schedules", conn)
        except Exception as e:
            logger.error(f"Error reading outlet schedules: {e}")
            return pd.DataFrame()

    def save_shift_templates(self, df: pd.DataFrame):
        """Save or update shift templates in local DuckDB database."""
        if df is None or df.empty:
            return
        try:
            with self.engine.begin() as conn:
                df.to_sql('shift_templates', conn, if_exists='replace', index=False)
            logger.info(f"Successfully saved {len(df)} shift templates to local database.")
        except Exception as e:
            logger.error(f"Error saving shift templates: {e}")

    def get_shift_templates(self) -> pd.DataFrame:
        """Fetch shift templates from local DuckDB database."""
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('shift_templates'):
                    return pd.DataFrame()
                return pd.read_sql("SELECT * FROM shift_templates", conn)
        except Exception as e:
            logger.error(f"Error reading shift templates: {e}")
            return pd.DataFrame()

    def save_outlet_template_assignments(self, df: pd.DataFrame):
        """Save outlet-to-template assignments in local DuckDB database."""
        if df is None or df.empty:
            return
        try:
            with self.engine.begin() as conn:
                df.to_sql('outlet_template_assignments', conn, if_exists='replace', index=False)
            logger.info(f"Successfully saved {len(df)} outlet template assignments.")
        except Exception as e:
            logger.error(f"Error saving outlet template assignments: {e}")

    def get_outlet_template_assignments(self) -> pd.DataFrame:
        """Fetch outlet-to-template assignments from local DuckDB database."""
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('outlet_template_assignments'):
                    return pd.DataFrame()
                return pd.read_sql("SELECT * FROM outlet_template_assignments", conn)
        except Exception as e:
            logger.error(f"Error reading outlet template assignments: {e}")
            return pd.DataFrame()

    def save_outlets(self, df: pd.DataFrame):
        """Save or update outlet records from a pandas DataFrame to local DuckDB."""
        if df is None or df.empty:
            return
        try:
            df_clean = df.copy()
            try:
                from modules.google_sheets import deduplicate_outlets_df
                df_clean = deduplicate_outlets_df(df_clean)
            except Exception:
                if 'Outlet' in df_clean.columns:
                    df_clean['Outlet'] = df_clean['Outlet'].astype(str).str.strip()
                    df_clean = df_clean[~df_clean['Outlet'].isin(['', 'None', 'nan', '<NA>', 'N/A'])]
                    df_clean = df_clean.drop_duplicates(subset=['Outlet'], keep='first')

            with self.engine.begin() as conn:
                df_clean.to_sql('outlets', conn, if_exists='replace', index=False)
            logger.info(f"Successfully saved {len(df_clean)} outlets to local database.")
        except Exception as e:
            logger.error(f"Error saving outlets to database: {e}")


    def get_outlets(self) -> pd.DataFrame:
        """Fetch all outlets from the database as a pandas DataFrame."""
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('outlets'):
                    return pd.DataFrame()
                return pd.read_sql("SELECT * FROM outlets", conn)
        except Exception as e:
            logger.error(f"Error reading outlets from database: {e}")
            return pd.DataFrame()


    def log_admin_action(self, action: str, details: str = None):
        """Log an administrative action to the database."""
        session = self.Session()
        try:
            log_entry = AdminLog(
                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                action=action,
                details=details
            )
            session.add(log_entry)
            session.commit()
            logger.info(f"Admin action logged: {action}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error logging admin action: {e}")
        finally:
            session.close()

    def get_admin_logs(self) -> list:
        """Fetch administrative logs."""
        session = self.Session()
        try:
            logs = session.query(AdminLog).order_by(AdminLog.id.desc()).all()
            return [{
                "id": log.id,
                "timestamp": log.timestamp,
                "action": log.action,
                "details": log.details
            } for log in logs]
        except Exception as e:
            logger.error(f"Error fetching admin logs: {e}")
            return []
        finally:
            session.close()

    def save_fingerprint_mappings(self, mappings: dict):
        """Clears existing fingerprint mappings and saves the new mappings to local DuckDB."""
        session = self.Session()
        try:
            # Delete all existing mappings
            session.query(FingerprintMapping).delete()
            # Insert the new mappings
            for fp_id, nrp in mappings.items():
                entry = FingerprintMapping(fingerprint_id=str(fp_id), NRP=str(nrp))
                session.add(entry)
            session.commit()
            logger.info(f"✅ Successfully cached {len(mappings)} fingerprint mappings to local DuckDB.")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error saving fingerprint mappings to DuckDB: {e}")
        finally:
            session.close()

    def get_fingerprint_mappings(self) -> dict:
        """Retrieves all cached fingerprint mappings from local DuckDB as a dictionary."""
        session = self.Session()
        try:
            rows = session.query(FingerprintMapping).all()
            return {r.fingerprint_id: r.NRP for r in rows}
        except Exception as e:
            logger.error(f"❌ Error reading fingerprint mappings from DuckDB: {e}")
            return {}
        finally:
            session.close()

    def delete_fingerprint_mapping_by_nrp(self, nrp: str):
        """Deletes fingerprint / face embedding mappings for a specific NRP from local DuckDB."""
        session = self.Session()
        try:
            nrp_clean = str(nrp).strip()
            session.query(FingerprintMapping).filter(FingerprintMapping.NRP == nrp_clean).delete()
            session.commit()
            logger.info(f"✅ Deleted fingerprint / face embedding mapping for NRP {nrp_clean} from local DuckDB.")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error deleting fingerprint mapping for NRP {nrp} from DuckDB: {e}")
        finally:
            session.close()

    def save_attendance_records(self, df_records: pd.DataFrame) -> int:
        """Append new attendance records, avoiding duplicates based on NRP + timestamp."""
        if df_records.empty:
            return 0
            
        try:
            from modules.google_sheets import populate_working_hours
            df_records = populate_working_hours(df_records)
        except Exception:
            pass

        session = self.Session()
        inserted_count = 0
        try:
            # Pre-fetch existing records to perform bulk comparison in memory
            nrps = df_records['NRP'].astype(str).str.strip().unique().tolist()
            timestamps = df_records['timestamp'].astype(str).str.strip().unique().tolist()
            
            existing_objs = session.query(AttendanceRecord).\
                filter(AttendanceRecord.NRP.in_(nrps), AttendanceRecord.timestamp.in_(timestamps)).all()
            existing_map = {(str(r.NRP).strip(), str(r.timestamp).strip()): r for r in existing_objs}
            
            for _, row in df_records.iterrows():
                nrp = str(row.get('NRP', '')).strip()
                timestamp = str(row.get('timestamp', '')).strip()
                if not nrp or not timestamp:
                    continue
                    
                dist_val = str(row.get('distance_meters', row.get('distance_meter', row.get('distance', '')))).strip()
                acc_val = str(row.get('gps_accuracy', row.get('accuracy', ''))).strip()
                outlet_val = str(row.get('outlet', row.get('Outlet', ''))).strip()
                ws_val = str(row.get('work_start', '')).strip()
                we_val = str(row.get('work_end', '')).strip()

                key = (nrp, timestamp)
                if key not in existing_map:
                    rec = AttendanceRecord(
                        NRP=nrp,
                        employee_name=str(row.get('employee_name', row.get('Nama Staff', 'Unknown'))).strip(),
                        timestamp=timestamp,
                        date=str(row.get('date', '')).strip()[:10],
                        time=str(row.get('time', '')).strip(),
                        type=str(row.get('type', row.get('attendance_type', 'CLOCK_IN'))).strip(),
                        status=str(row.get('status', '')).strip(),
                        timezone=str(row.get('timezone', 'WIB')).strip(),
                        outlet=outlet_val,
                        distance_meters=dist_val,
                        gps_accuracy=acc_val,
                        work_start=ws_val,
                        work_end=we_val,
                        notes=str(row.get('notes', '')).strip(),
                        fingerprint_id=str(row.get('fingerprint_id', '')).strip()
                    )
                    session.add(rec)
                    inserted_count += 1
                else:
                    existing_rec = existing_map[key]
                    new_notes = str(row.get('notes', '')).strip()
                    if new_notes and new_notes.lower() not in ['nan', 'none'] and existing_rec.notes != new_notes:
                        existing_rec.notes = new_notes
                    if dist_val and dist_val.lower() not in ['nan', 'none']:
                        existing_rec.distance_meters = dist_val
                    if acc_val and acc_val.lower() not in ['nan', 'none']:
                        existing_rec.gps_accuracy = acc_val
                    if outlet_val and outlet_val.lower() not in ['nan', 'none']:
                        existing_rec.outlet = outlet_val
                    if ws_val and ws_val.lower() not in ['nan', 'none']:
                        existing_rec.work_start = ws_val
                    if we_val and we_val.lower() not in ['nan', 'none']:
                        existing_rec.work_end = we_val
            session.commit()
            logger.info(f"Appended {inserted_count} new attendance records to local DB.")
            return inserted_count
        except Exception as e:
            session.rollback()
            logger.error(f"Error appending attendance records: {e}")
            return 0
        finally:
            session.close()

    def get_attendance_records(self, start_date: str = None, end_date: str = None, nrp: str = None) -> pd.DataFrame:
        """Fetch attendance records using fast SQL query execution (bypassing ORM)."""
        query = """
            SELECT 
                ar.*, 
                sa.svp_nrp AS "SVP NRP", 
                sa.svp_name AS "SVP Name", 
                CASE 
                    WHEN LOWER(ar.notes) LIKE '%supervisor approval required%' THEN 
                        CASE 
                            WHEN sa.review_status IS NULL OR sa.review_status = '' THEN 'Waiting for Supervisor approval'
                            ELSE sa.review_status
                        END
                    ELSE sa.review_status
                END AS "Review Status",
                sa.review_date AS "Review Date"
            FROM attendance_records ar
            LEFT JOIN svp_approvals sa 
                ON ar.NRP = sa.NRP AND ar.timestamp = sa.timestamp
            WHERE 1=1
        """
        params = {}
        
        if start_date:
            query += " AND SUBSTR(ar.date, 1, 10) >= :start_date"
            params["start_date"] = start_date
        if end_date:
            query += " AND SUBSTR(ar.date, 1, 10) <= :end_date"
            params["end_date"] = end_date
        if nrp:
            query += " AND ar.NRP = :nrp"
            params["nrp"] = nrp
            
        query += " ORDER BY ar.timestamp DESC"
        
        try:
            with self.engine.connect() as conn:
                from sqlalchemy import text
                df = pd.read_sql_query(text(query), conn, params=params)
                
                if not df.empty and "notes" in df.columns:
                    df["Case Category"] = df["notes"].apply(parse_case_category)
                    svp_parsed = df.apply(parse_svp_details_from_notes, axis=1)
                    df["SVP NRP"] = svp_parsed["SVP NRP"]
                    df["SVP Name"] = svp_parsed["SVP Name"]
                    df["Review Status"] = svp_parsed["Review Status"]
                    df["Review Date"] = svp_parsed["Review Date"]
                else:
                    df["Case Category"] = ""

                try:
                    from modules.google_sheets import populate_working_hours
                    df = populate_working_hours(df)
                except Exception:
                    pass

                return df
        except Exception as e:
            logger.error(f"Error reading filtered attendance records: {e}")
            return pd.DataFrame()

    def get_employee_today_scans(self, nrp: str, date_str: str) -> list:
        """Fetch today's scans for an employee from the local database."""
        session = self.Session()
        try:
            query = session.query(AttendanceRecord).filter_by(NRP=nrp, date=date_str).all()
            records = []
            for rec in query:
                records.append({
                    "NRP": rec.NRP,
                    "employee_name": rec.employee_name,
                    "timestamp": rec.timestamp,
                    "date": rec.date,
                    "time": rec.time,
                    "type": rec.type,
                    "status": rec.status,
                    "timezone": rec.timezone,
                    "work_start": rec.work_start,
                    "work_end": rec.work_end,
                    "notes": rec.notes,
                    "fingerprint_id": rec.fingerprint_id
                })
            return records
        except Exception as e:
            logger.error(f"Error reading employee today scans: {e}")
            return []
        finally:
            session.close()

    def get_purge_candidates_count(self, months: int) -> int:
        """Count attendance records older than 'months' from today."""
        session = self.Session()
        try:
            threshold_date = datetime.now() - timedelta(days=months * 30)
            threshold_str = threshold_date.strftime('%Y-%m-%d')
            count = session.query(AttendanceRecord).filter(AttendanceRecord.date < threshold_str).count()
            return count
        except Exception as e:
            logger.error(f"Error checking purge candidates count: {e}")
            return 0
        finally:
            session.close()

    def purge_attendance_records(self, months: int) -> int:
        """Archive records older than 'months' to local CSV and then purge them from DB."""
        session = self.Session()
        try:
            threshold_date = datetime.now() - timedelta(days=months * 30)
            threshold_str = threshold_date.strftime('%Y-%m-%d')
            
            # Query candidate records to archive
            records_to_archive = session.query(AttendanceRecord).filter(AttendanceRecord.date < threshold_str).all()
            
            if records_to_archive:
                import pandas as pd
                archived_list = []
                for rec in records_to_archive:
                    archived_list.append({
                        "id": rec.id,
                        "NRP": rec.NRP,
                        "employee_name": rec.employee_name,
                        "timestamp": rec.timestamp,
                        "date": rec.date,
                        "time": rec.time,
                        "type": rec.type,
                        "status": rec.status,
                        "timezone": rec.timezone,
                        "work_start": rec.work_start,
                        "work_end": rec.work_end,
                        "notes": rec.notes,
                        "fingerprint_id": rec.fingerprint_id
                    })
                df_archive = pd.DataFrame(archived_list)
                
                # Resolve archive folder path under the db_path's parent directory
                db_dir = os.path.dirname(os.path.abspath(self.db_path))
                archive_dir = os.path.join(db_dir, "archive")
                os.makedirs(archive_dir, exist_ok=True)
                
                timestamp_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
                archive_file = os.path.join(archive_dir, f"attendance_archive_{timestamp_suffix}.csv")
                
                df_archive.to_csv(archive_file, index=False)
                logger.info(f"Archived {len(df_archive)} records to {archive_file}")
                
                # Delete records where date is older than threshold
                deleted = session.query(AttendanceRecord).filter(AttendanceRecord.date < threshold_str).delete()
                session.commit()
                logger.info(f"Purged {deleted} attendance records older than {months} months ({threshold_str}).")
                return deleted
            else:
                logger.info(f"No candidates to purge or archive older than {months} months.")
                return 0
        except Exception as e:
            session.rollback()
            logger.error(f"Error purging attendance records: {e}")
            return 0
        finally:
            session.close()

    def init_admin_credentials(self):
        """Initialize the admin credentials if the table is empty or missing."""
        try:
            with self.engine.begin() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('admin_credentials'):
                    conn.execute(text("CREATE TABLE admin_credentials (username VARCHAR PRIMARY KEY, password_hash VARCHAR)"))
                
                # Check if admin user exists
                res = conn.execute(text("SELECT count(*) FROM admin_credentials WHERE username = 'admin'")).fetchone()
                if res[0] == 0:
                    conn.execute(text("INSERT INTO admin_credentials (username, password_hash) VALUES ('admin', :phash)"), {"phash": DEFAULT_HASH})
                    logger.info("Successfully initialized default admin credentials with username 'admin'.")
        except Exception as e:
            logger.error(f"Error initializing admin credentials: {e}")

    def authenticate_admin(self, username, password) -> bool:
        """Authenticate administrative user credentials."""
        try:
            import hashlib
            password_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
            with self.engine.connect() as conn:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                if not inspector.has_table('admin_credentials'):
                    return False
                res = conn.execute(
                    text("SELECT password_hash FROM admin_credentials WHERE username = :uname"),
                    {"uname": username}
                ).fetchone()
                if res and res[0] == password_hash:
                    return True
        except Exception as e:
            logger.error(f"Error authenticating admin: {e}")
        return False

    def update_admin_password(self, username, new_password) -> bool:
        """Update administrative password."""
        try:
            import hashlib
            new_hash = hashlib.sha256(new_password.encode('utf-8')).hexdigest()
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE admin_credentials SET password_hash = :phash WHERE username = :uname"),
                    {"phash": new_hash, "uname": username}
                )
                return True
        except Exception as e:
            logger.error(f"Error updating admin password: {e}")
        return False

    def reset_admin_password(self, username) -> bool:
        """Reset administrative password to default hash."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE admin_credentials SET password_hash = :phash WHERE username = :uname"),
                    {"phash": DEFAULT_HASH, "uname": username}
                )
                return True
        except Exception as e:
            logger.error(f"Error resetting admin password: {e}")
        return False

    def save_svp_approval_records(self, df_approvals: pd.DataFrame) -> int:
        """Append or update supervisor approval records, avoiding duplicates based on NRP + timestamp."""
        if df_approvals.empty:
            return 0
            
        session = self.Session()
        upserted_count = 0
        try:
            # Normalize column names to lower case and strip spaces
            df_cleaned = df_approvals.copy()
            df_cleaned.columns = [str(c).strip().lower() for c in df_cleaned.columns]
            
            # Map column indices or names
            nrp_col = next((c for c in df_cleaned.columns if c == 'nrp'), None)
            ts_col = next((c for c in df_cleaned.columns if c in ['timestamp', 'time_stamp']), None)
            svp_nrp_col = next((c for c in df_cleaned.columns if c in ['svp nrp', 'svp_nrp']), None)
            svp_name_col = next((c for c in df_cleaned.columns if c in ['svp name', 'svp_name']), None)
            status_col = next((c for c in df_cleaned.columns if c in ['review status', 'review_status']), None)
            date_col = next((c for c in df_cleaned.columns if c in ['review date', 'review_date']), None)
            
            for _, row in df_cleaned.iterrows():
                nrp = str(row.get(nrp_col, '')).strip() if nrp_col else ""
                timestamp = str(row.get(ts_col, '')).strip() if ts_col else ""
                if not nrp or not timestamp:
                    continue
                    
                # Query if existing record exists to update it
                existing = session.query(SvpApproval).filter_by(NRP=nrp, timestamp=timestamp).first()
                
                svp_nrp_val = str(row.get(svp_nrp_col, '')).strip() if svp_nrp_col else ""
                svp_name_val = str(row.get(svp_name_col, '')).strip() if svp_name_col else ""
                status_val = str(row.get(status_col, '')).strip() if status_col else ""
                date_val = str(row.get(date_col, '')).strip() if date_col else ""
                
                if existing:
                    existing.svp_nrp = svp_nrp_val
                    existing.svp_name = svp_name_val
                    existing.review_status = status_val
                    existing.review_date = date_val
                else:
                    rec = SvpApproval(
                        NRP=nrp,
                        timestamp=timestamp,
                        svp_nrp=svp_nrp_val,
                        svp_name=svp_name_val,
                        review_status=status_val,
                        review_date=date_val
                    )
                    session.add(rec)
                upserted_count += 1
                
            session.commit()
            logger.info(f"Upserted {upserted_count} SVP approval records to local DB.")
            return upserted_count
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving SVP approval records: {e}")
            return 0
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # DEVICE BIND / UNBIND HISTORY  (fraud detection audit trail)         #
    # ------------------------------------------------------------------ #

    def save_device_bind_history(self, df: pd.DataFrame) -> int:
        """Bulk-upsert device bind/unbind history rows from a DataFrame.
        
        Expected columns (case-insensitive, spaces OK):
            Timestamp, NRP, Device_ID, Action, Triggered_By, Notes
        """
        if df is None or df.empty:
            return 0

        session = self.Session()
        inserted = 0
        try:
            df_c = df.copy()
            df_c.columns = [str(c).strip().lower().replace(' ', '_') for c in df_c.columns]

            col = lambda *names: next((c for c in df_c.columns if c in names), None)
            ts_col  = col('timestamp')
            nrp_col = col('nrp')
            dev_col = col('device_id', 'deviceid', 'device id')
            act_col = col('action')
            trg_col = col('triggered_by', 'triggered by')
            nt_col  = col('notes')

            for _, row in df_c.iterrows():
                ts  = str(row.get(ts_col,  '')).strip() if ts_col  else ''
                nrp = str(row.get(nrp_col, '')).strip() if nrp_col else ''
                dev = str(row.get(dev_col, '')).strip() if dev_col else ''
                act = str(row.get(act_col, '')).strip() if act_col else ''
                trg = str(row.get(trg_col, '')).strip() if trg_col else ''
                nt  = str(row.get(nt_col,  '')).strip() if nt_col  else ''

                if not nrp or not ts:
                    continue

                # Avoid exact duplicates (same NRP + device + timestamp)
                exists = session.query(DeviceBindHistory).filter_by(
                    NRP=nrp, device_id=dev, timestamp=ts
                ).first()
                if not exists:
                    session.add(DeviceBindHistory(
                        timestamp=ts, NRP=nrp, device_id=dev,
                        action=act, triggered_by=trg, notes=nt
                    ))
                    inserted += 1

            session.commit()
            logger.info(f"Inserted {inserted} device bind history rows to local DB.")
            return inserted
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving device bind history: {e}")
            return 0
        finally:
            session.close()

    def get_device_bind_history(
        self,
        nrp: str = None,
        device_id: str = None
    ) -> pd.DataFrame:
        """Retrieve device bind/unbind history, optionally filtered by NRP or device_id.
        
        Returns a DataFrame with columns:
            id, timestamp, NRP, device_id, action, triggered_by, notes
        """
        session = self.Session()
        try:
            q = session.query(DeviceBindHistory)
            if nrp:
                q = q.filter(DeviceBindHistory.NRP == str(nrp).strip())
            if device_id:
                q = q.filter(DeviceBindHistory.device_id == str(device_id).strip())
            q = q.order_by(DeviceBindHistory.timestamp.desc())
            rows = q.all()
            if not rows:
                return pd.DataFrame(
                    columns=['id', 'timestamp', 'NRP', 'device_id', 'action', 'triggered_by', 'notes']
                )
            return pd.DataFrame([{
                'id':           r.id,
                'timestamp':    r.timestamp,
                'NRP':          r.NRP,
                'device_id':    r.device_id,
                'action':       r.action,
                'triggered_by': r.triggered_by,
                'notes':        r.notes,
            } for r in rows])
        except Exception as e:
            logger.error(f"Error getting device bind history: {e}")
            return pd.DataFrame()
        finally:
            session.close()
