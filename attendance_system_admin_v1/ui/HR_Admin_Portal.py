import os
import json
import time
import re
import logging
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st
import plotly.express as px
from modules.database import DatabaseHandler
from modules.google_sheets import GoogleSheetsHandler
from modules.biometric_engine import parse_fingerprint_id

logger = logging.getLogger("HRAdminPortal")

# Resolve project root relative to this file
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_profile_thresholds():
    defaults = {
        "p_absent_thresh": 0.35,
        "p_missing_thresh": 0.15,
        "p_slipping_thresh1": 0.15,
        "p_slipping_thresh2": 0.25,
        "p_compensatory_thresh": 0.20,
        "p_late_thresh": 0.25,
        "p_early_thresh": 0.25,
        "p_overtime_thresh": 0.30,
        "p_break_thresh": 0.25,
        "p_break_mins": 60,
        "early_in_thresh_min": 10,
        "clock_in_grace_min": 10,
        "clock_out_grace_min": 10,
        "late_out_thresh_min": 120
    }
    file_path = os.path.join(PROJECT_ROOT, "data", "profile_thresholds.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with defaults to ensure all keys are present
                for k, v in defaults.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception:
            pass
    return defaults


def save_profile_thresholds(thresholds):
    file_path = os.path.join(PROJECT_ROOT, "data", "profile_thresholds.json")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(thresholds, f, indent=4)
        return True
    except Exception:
        return False


def extract_face_vectors_from_mappings(mappings_dict: dict) -> dict:
    """Extract raw float vector per NRP from mappings dict {embedding_str: nrp} or {nrp: embedding_str}."""
    nrp_vectors = {}
    if not mappings_dict:
        return nrp_vectors
    for k, v in mappings_dict.items():
        nrp = None
        emb_str = None
        if isinstance(k, str) and k.startswith("[") and k.endswith("]"):
            emb_str = k
            nrp = str(v).strip()
        elif isinstance(v, str) and v.startswith("[") and v.endswith("]"):
            emb_str = v
            nrp = str(k).strip()
        
        if nrp and emb_str:
            try:
                parsed = json.loads(emb_str)
                if isinstance(parsed, list) and len(parsed) >= 10:
                    nrp_vectors[nrp] = [float(x) for x in parsed]
            except Exception:
                pass
    return nrp_vectors


def compute_face_similarity_pairs(face_embs: dict, df_employees: pd.DataFrame, threshold_pct: float) -> list:
    """Compute pairwise face embedding similarity using biometric Euclidean distance thresholding."""
    import numpy as np
    
    similar_pairs = []
    if not face_embs or len(face_embs) < 2:
        return similar_pairs
        
    nrp_list = sorted(list(face_embs.keys()))
    
    # Pre-map employee name from df_employees
    emp_name_map = {}
    if df_employees is not None and not df_employees.empty and 'NRP' in df_employees.columns:
        if 'Nama Staff' in df_employees.columns:
            emp_name_map = df_employees.set_index('NRP')['Nama Staff'].to_dict()
        elif 'Nama' in df_employees.columns:
            emp_name_map = df_employees.set_index('NRP')['Nama'].to_dict()
            
    for i in range(len(nrp_list)):
        for j in range(i + 1, len(nrp_list)):
            nrp1 = nrp_list[i]
            nrp2 = nrp_list[j]
            vec1 = np.array(face_embs[nrp1], dtype=float)
            vec2 = np.array(face_embs[nrp2], dtype=float)
            
            if len(vec1) == len(vec2) and len(vec1) > 0:
                # Jarak Euclidean Biometrik (Standar face-api.js/FaceNet: d < 0.40 = Duplikat/Mirip, d >= 0.55 = Berbeda)
                dist = float(np.linalg.norm(vec1 - vec2))
                
                # Konversi Jarak Euclidean ke Persentase Identitas Wajah
                # Jarak 0.0 -> 100%, Jarak 0.40 -> 80%, Jarak 0.55 -> 50%
                if dist < 0.55:
                    sim_pct = max(0.0, min(100.0, float((1.0 - (dist / 0.55)) * 100.0)))
                    
                    if sim_pct >= threshold_pct:
                        risk_level = "🔴 HIGH RISK (Identik)" if dist < 0.38 else "🟡 MEDIUM RISK (Mirip)"
                        similar_pairs.append({
                            "NRP 1": nrp1,
                            "Nama Karyawan 1": str(emp_name_map.get(nrp1, "Unknown")),
                            "NRP 2": nrp2,
                            "Nama Karyawan 2": str(emp_name_map.get(nrp2, "Unknown")),
                            "Kemiripan Wajah (%)": f"{sim_pct:.1f}%",
                            "Sim_Val": sim_pct,
                            "Tingkat Risiko": risk_level
                        })
                        
    similar_pairs.sort(key=lambda x: x["Sim_Val"], reverse=True)
    return similar_pairs


def calculate_scan_status(row, early_in_thresh=10, clock_in_grace=10, clock_out_grace=10, late_out_thresh=120):
    """
    Calculates attendance scan status based on type, timestamp/time, work_start, work_end, and threshold settings.

    A. Clock-in Scan:
       - Early In: Checked in > early_in_thresh minutes before shift start.
       - On Time: Checked in within clock_in_grace minutes prior to shift start (or at shift start).
       - Late In: Checked in after scheduled shift start.

    B. Clock-out Scan:
       - Early Out: Clocked out before scheduled shift end.
       - On Time: Clocked out within clock_out_grace minutes after shift end.
       - Late Out: Clocked out between clock_out_grace minutes and late_out_thresh minutes (e.g. 2h) after shift end.
       - Over Time: Clocked out more than late_out_thresh minutes (e.g. 2h) after shift end.
    """
    scan_type_raw = str(row.get('type', '') or '').strip().upper()
    scan_status_raw = str(row.get('status', '') or '').strip()

    # Preserve break scan status
    if scan_status_raw in ['Start Break', 'End Break'] or 'BREAK' in scan_type_raw:
        return scan_status_raw if scan_status_raw else ('Start Break' if 'START' in scan_type_raw else 'End Break')

    is_clock_in = scan_type_raw in ['CLOCK_IN', 'CLOCK-IN', 'IN', 'MASUK', 'CHECK_IN', 'CHECK-IN']
    is_clock_out = scan_type_raw in ['CLOCK_OUT', 'CLOCK-OUT', 'OUT', 'KELUAR', 'CHECK_OUT', 'CHECK-OUT']

    if not is_clock_in and not is_clock_out:
        if scan_status_raw in ['Early In', 'On Time', 'Late In']:
            is_clock_in = True
        elif scan_status_raw in ['Early Out', 'Late Out', 'Over Time']:
            is_clock_out = True

    def time_to_minutes(t_val):
        if not t_val or pd.isna(t_val):
            return None
        t_str = str(t_val).strip()
        if ' ' in t_str:
            parts_space = t_str.split(' ')
            t_str = parts_space[1] if len(parts_space) > 1 else parts_space[0]
        parts = t_str.split(':')
        if len(parts) >= 2:
            try:
                return int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                return None
        return None

    scan_mins = time_to_minutes(row.get('time'))
    if scan_mins is None:
        scan_mins = time_to_minutes(row.get('timestamp'))
    if scan_mins is None:
        return scan_status_raw if scan_status_raw else 'Normal'

    work_start_mins = time_to_minutes(row.get('work_start'))
    work_end_mins = time_to_minutes(row.get('work_end'))

    if work_start_mins is None:
        work_start_mins = 480  # Default 08:00
    if work_end_mins is None:
        work_end_mins = 1020  # Default 17:00

    if is_clock_in:
        diff = scan_mins - work_start_mins
        if diff < -early_in_thresh:
            return 'Early In'
        elif diff <= 0:
            return 'On Time'
        else:
            return 'Late In'
    elif is_clock_out:
        diff = scan_mins - work_end_mins
        if diff < 0:
            return 'Early Out'
        elif diff <= clock_out_grace:
            return 'On Time'
        elif diff <= late_out_thresh:
            return 'Late Out'
        else:
            return 'Over Time'

    return scan_status_raw if scan_status_raw else 'Normal'



def aggregate_employee_profile(df_employee_daily_patterns, hk=None):
    """
    Aggregates daily attendance patterns for a single employee over the date range
    to classify their overall behavior profile using custom parameter variables in st.session_state.
    """
    total_days = len(df_employee_daily_patterns)
    if total_days == 0:
        return "No Data"

    # Get parameters from session state or use defaults
    p_absent_thresh = st.session_state.get("p_absent_thresh", 0.35)
    p_missing_thresh = st.session_state.get("p_missing_thresh", 0.15)
    p_slipping_thresh1 = st.session_state.get("p_slipping_thresh1", 0.15)
    p_slipping_thresh2 = st.session_state.get("p_slipping_thresh2", 0.25)
    p_compensatory_thresh = st.session_state.get("p_compensatory_thresh", 0.20)
    p_late_thresh = st.session_state.get("p_late_thresh", 0.25)
    p_early_thresh = st.session_state.get("p_early_thresh", 0.25)
    p_overtime_thresh = st.session_state.get("p_overtime_thresh", 0.30)
    p_break_thresh = st.session_state.get("p_break_thresh", 0.25)
    p_break_mins = st.session_state.get("p_break_mins", 60)

    # Check which column is present (tolerate either Pattern or Profile during transition)
    col = 'Pattern' if 'Pattern' in df_employee_daily_patterns.columns else 'Profile'
    pattern_counts = df_employee_daily_patterns[col].value_counts()
    
    # Calculate counts
    absent_count = pattern_counts.get("Absent (No-Show)", 0)
    missing_in = pattern_counts.get("Missing Clock In", 0)
    missing_out = pattern_counts.get("Missing Clock Out", 0)
    total_missing = missing_in + missing_out
    
    active_days = total_days - absent_count
    
    # 1. Condition: If active_days == 0 then "No Records"
    if active_days == 0:
        return "No Records"

    # Resolve HK (Hari Kerja) from parameter, DataFrame column, or fallback to total_days
    effective_hk = None
    if hk is not None and pd.notna(hk):
        try:
            val = float(hk)
            if val > 0:
                effective_hk = val
        except (ValueError, TypeError):
            pass

    if effective_hk is None and 'HK' in df_employee_daily_patterns.columns and not df_employee_daily_patterns['HK'].empty:
        try:
            val = float(df_employee_daily_patterns['HK'].iloc[0])
            if val > 0:
                effective_hk = val
        except (ValueError, TypeError):
            pass

    if effective_hk is None or effective_hk <= 0:
        effective_hk = float(total_days)

    # 2. Chronic Absentee (absent_count / HK > p_absent_thresh)
    if effective_hk > 0 and (absent_count / effective_hk) > p_absent_thresh:
        return "Chronic Absentee"

    # 3. New category: Over-Active Worker (active_days > HK)
    if effective_hk > 0 and active_days > effective_hk:
        return "Over-Active Worker"

    if active_days > 0:
        # 4. Forgetful Logger
        if total_missing / active_days > p_missing_thresh:
            return "Forgetful Logger"
            
        # Count late check-ins, early check-outs, and overtime check-outs
        late_in_count = 0
        early_out_count = 0
        overtime_count = 0
        compensatory_count = 0
        slipping_count = 0
        
        for pat, count in pattern_counts.items():
            if " / " in pat:
                parts = pat.split(" / ", 1)
                if len(parts) == 2:
                    in_p, out_p = parts
                    is_late = (in_p == "Late In")
                    is_early = (out_p == "Early Out")
                    is_overtime = (out_p in ["Over Time"]) # take out "Late Out" --> Loyalitas
                    
                    if is_late and is_overtime:
                        compensatory_count += count
                    elif is_late and is_early:
                        slipping_count += count
                    else:
                        if is_late:
                            late_in_count += count
                        if is_early:
                            early_out_count += count
                        if is_overtime:
                            overtime_count += count

        # Count extended break days
        extended_break_days = 0
        if 'Break (mins)' in df_employee_daily_patterns.columns:
            try:
                breaks = pd.to_numeric(df_employee_daily_patterns['Break (mins)'], errors='coerce').fillna(0)
                extended_break_days = len(df_employee_daily_patterns[breaks > p_break_mins])
            except Exception:
                pass

        # 5. Slipping Attendance / Habitual Late & Early
        if slipping_count / active_days > p_slipping_thresh1 or (late_in_count > 0 and early_out_count > 0 and (late_in_count + early_out_count) / active_days > p_slipping_thresh2):
            return "Slipping Attendance / Habitual Late & Early"

        # 6. Compensatory Hard Worker (Arrives late, stays late)
        if compensatory_count / active_days > p_compensatory_thresh:
            return "Compensatory Hard Worker"

        # 7. Habitual Late Comer (Arrives late, leaves on time)
        if (late_in_count + compensatory_count + slipping_count) / active_days > p_late_thresh:
            return "Habitual Late Comer"

        # 8. Early Clock-Out Specialist (Arrives on time, leaves early)
        if (early_out_count + slipping_count) / active_days > p_early_thresh:
            return "Early Clock-Out Specialist"

        # 9. Diligent Overachiever (Arrives on time, works overtime)
        if overtime_count / active_days > p_overtime_thresh:
            return "Diligent Overachiever"

        # 10. Extended Breaker
        if extended_break_days / active_days > p_break_thresh:
            return "Extended Breaker"

    # 11. Standard/Disciplined Worker
    return "Standard/Disciplined Worker"


@st.cache_data(ttl=600)
def load_employees_cached(_db_handler):
    """Fetch employee list with 10-minute caching."""
    return _db_handler.get_employees()


@st.cache_data(ttl=60)
def load_outlets_cached(_db_handler):
    """Fetch outlet list with caching."""
    return _db_handler.get_outlets()


@st.cache_data(ttl=10)
def load_attendance_cached(_db_handler, start_date_str=None, end_date_str=None):
    """Fetch attendance records using database-level filtering and short caching."""
    return _db_handler.get_attendance_records(start_date=start_date_str, end_date=end_date_str)



@st.cache_data(ttl=300)
def compute_analytics(df_att, df_employees, df_outlets=None, selected_outlet="All Outlets", selected_am="All Area Managers", selected_position="All Positions", start_date=None, end_date=None, selected_profile_type="All"):
    """Compute comprehensive and advanced attendance behavior metrics."""
    total_outlets_cnt = 0
    if df_outlets is not None and not df_outlets.empty:
        if 'Outlet' in df_outlets.columns:
            total_outlets_cnt = len([x for x in df_outlets['Outlet'].dropna().unique() if str(x).strip() and str(x).strip().lower() not in ['nan', 'none']])
        else:
            total_outlets_cnt = len(df_outlets)
    elif not df_employees.empty and 'Outlet' in df_employees.columns:
        total_outlets_cnt = len([x for x in df_employees['Outlet'].dropna().unique() if str(x).strip() and str(x).strip().lower() not in ['nan', 'none']])

    # Handle selected_outlet whether it's a list, tuple, set, or single string
    if isinstance(selected_outlet, (list, tuple, set)):
        selected_outlets_list = [str(x).strip() for x in selected_outlet if str(x).strip() and str(x).strip() != "All Outlets"]
    elif isinstance(selected_outlet, str) and selected_outlet.strip() != "All Outlets" and selected_outlet.strip() != "":
        selected_outlets_list = [selected_outlet.strip()]
    else:
        selected_outlets_list = []

    if selected_outlets_list:
        total_outlets_cnt = len(selected_outlets_list)

    analytics = {
        "total_scans": 0,
        "total_outlets": total_outlets_cnt,
        "total_active_staff": len(df_employees),
        "late_in_count": 0,
        "late_in_rate": 0.0,
        "on_time_rate": 0.0,
        "avg_break_duration": 0.0,
        "early_out_count": 0,
        "early_out_rate": 0.0,
        "avg_working_hours": 0.0,
        "avg_daily_headcount": 0.0,
        "avg_daily_overtime": 0.0,
        "daily_scans": pd.DataFrame(),
        "top_late_staff": pd.DataFrame(),
        "unregistered_scans": 0,
        "hourly_distribution": pd.DataFrame(),
        "status_distribution": pd.DataFrame(),
        "checkout_status_distribution": pd.DataFrame(),
        "top_early_out_staff": pd.DataFrame(),
        "top_overtime_staff": pd.DataFrame(),
        "outlet_performance": pd.DataFrame(),
        "svp_approval_distribution": pd.DataFrame(),
        "svp_approval_table": pd.DataFrame(),
        "abnormal_shifts": []
    }

    
    # Handle multiselect list/tuple/set/str for selected_am and selected_position
    if isinstance(selected_am, (list, tuple, set)):
        selected_am_list = [str(x).strip() for x in selected_am if str(x).strip() and str(x).strip() != "All Area Managers"]
    elif isinstance(selected_am, str) and selected_am.strip() != "All Area Managers" and selected_am.strip() != "":
        selected_am_list = [selected_am.strip()]
    else:
        selected_am_list = []

    if isinstance(selected_position, (list, tuple, set)):
        selected_pos_list = [str(x).strip() for x in selected_position if str(x).strip() and str(x).strip() != "All Positions"]
    elif isinstance(selected_position, str) and selected_position.strip() != "All Positions" and selected_position.strip() != "":
        selected_pos_list = [selected_position.strip()]
    else:
        selected_pos_list = []

    # Filter active staff based on all filters (Outlet, AM Baru, Position)
    df_emp_filtered = df_employees.copy() if not df_employees.empty else pd.DataFrame()
    if not df_emp_filtered.empty:
        if selected_outlets_list:
            df_emp_filtered = df_emp_filtered[df_emp_filtered['Outlet'].isin(selected_outlets_list)]
        if 'AM Baru' in df_emp_filtered.columns and selected_am_list:
            df_emp_filtered = df_emp_filtered[df_emp_filtered['AM Baru'].isin(selected_am_list)]
        pos_col = 'Posisi Update' if 'Posisi Update' in df_emp_filtered.columns else ('Posisi' if 'Posisi' in df_emp_filtered.columns else None)
        if pos_col and selected_pos_list:
            df_emp_filtered = df_emp_filtered[df_emp_filtered[pos_col].isin(selected_pos_list)]
            
    analytics["total_active_staff"] = len(df_emp_filtered)
    
    if df_att.empty:
        return analytics
        
    # Unregistered scans count
    df_valid = df_att[df_att['NRP'].astype(str) != "Unknown"].copy()
    analytics["unregistered_scans"] = len(df_att) - len(df_valid)
    
    if df_emp_filtered.empty:
        return analytics

    # Merge attendance scans with employee info
    df_merged = pd.merge(df_att, df_emp_filtered, on='NRP', how='inner')
    if df_merged.empty:
        return analytics

    # Parse timestamp columns to datetime and date
    df_merged['dt'] = pd.to_datetime(df_merged['timestamp'], errors='coerce', format='mixed')
    df_merged['date_dt'] = pd.to_datetime(df_merged['date'], errors='coerce', format='mixed').dt.date
    df_merged = df_merged.dropna(subset=['dt'])

    # Apply date filters
    if start_date and end_date:
        df_merged = df_merged[(df_merged['date_dt'] >= start_date) & (df_merged['date_dt'] <= end_date)]

    if df_merged.empty:
        return analytics

    # Helper function to convert time string to minutes
    def parse_to_minutes(t_str):
        if not t_str or pd.isna(t_str):
            return None
        try:
            parts = str(t_str).split(':')
            if len(parts) >= 2:
                return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
        return None

    # Calculate status overrides and overtime hours on the fly
    early_in_m = st.session_state.get("early_in_thresh_min", 10)
    clock_in_g = st.session_state.get("clock_in_grace_min", 10)
    clock_out_g = st.session_state.get("clock_out_grace_min", 10)
    late_out_m = st.session_state.get("late_out_thresh_min", 120)

    df_merged['status'] = df_merged.apply(
        lambda r: calculate_scan_status(
            r,
            early_in_thresh=early_in_m,
            clock_in_grace=clock_in_g,
            clock_out_grace=clock_out_g,
            late_out_thresh=late_out_m
        ),
        axis=1
    )
    df_merged['overtime_hours'] = 0.0

    time_parts = df_merged['time'].astype(str).str.split(':')
    h_out = pd.to_numeric(time_parts.str[0], errors='coerce')
    m_out = pd.to_numeric(time_parts.str[1], errors='coerce')
    t_out_mins = h_out * 60 + m_out

    work_end_parts = df_merged['work_end'].astype(str).str.split(':')
    h_end = pd.to_numeric(work_end_parts.str[0], errors='coerce')
    m_end = pd.to_numeric(work_end_parts.str[1], errors='coerce')
    t_end_mins = h_end * 60 + m_end

    diff_minutes = t_out_mins - t_end_mins
    is_clock_out = df_merged['type'] == 'CLOCK_OUT'
    has_work_end = df_merged['work_end'].notna() & (df_merged['work_end'] != '')
    is_ot = is_clock_out & has_work_end & (diff_minutes > late_out_m)

    df_merged.loc[is_ot, 'overtime_hours'] = (diff_minutes[is_ot] // 60).fillna(0.0)

    analytics["total_scans"] = len(df_merged)


    # Filter active staff in filtered subset
    # Employee active staff count is already set using df_emp_filtered at the start

    # 1. Check-In & Check-Out Metrics (Daily Work Start & End)
    clock_ins = df_merged[(df_merged['type'] == 'CLOCK_IN') & (~df_merged['status'].isin(['End Break', 'Start Break']))]
    clock_outs = df_merged[(df_merged['type'] == 'CLOCK_OUT') & (~df_merged['status'].isin(['Start Break', 'End Break']))]

    # Clock-in analytics
    if not clock_ins.empty:
        late_ins = clock_ins[clock_ins['status'] == 'Late In']
        on_time_ins = clock_ins[clock_ins['status'].isin(['On Time', 'Early In'])]
        analytics["late_in_count"] = len(late_ins)
        analytics["late_in_rate"] = (len(late_ins) / len(clock_ins)) * 100
        analytics["on_time_rate"] = (len(on_time_ins) / len(clock_ins)) * 100

        # Frequently late staff (Top 5)
        if len(late_ins) > 0:
            late_group = late_ins.groupby(['NRP', 'employee_name']).size().reset_index(name='Late Count')
            analytics["top_late_staff"] = late_group.sort_values(by='Late Count', ascending=False).head(5)

    # Clock-out analytics
    if not clock_outs.empty:
        early_outs = clock_outs[clock_outs['status'] == 'Early Out']
        analytics["early_out_count"] = len(early_outs)
        analytics["early_out_rate"] = (len(early_outs) / len(clock_outs)) * 100

        # Frequently early out staff (Top 5)
        if len(early_outs) > 0:
            early_out_group = early_outs.groupby(['NRP', 'employee_name']).size().reset_index(name='Early Out Count')
            analytics["top_early_out_staff"] = early_out_group.sort_values(by='Early Out Count', ascending=False).head(5)

        # Frequently overtime staff (Top 5 by total hours)
        over_times = clock_outs[clock_outs['status'] == 'Over Time']
        if not over_times.empty:
            ot_group = over_times.groupby(['NRP', 'employee_name'])['overtime_hours'].sum().reset_index(name='Total Overtime Hours')
            analytics["top_overtime_staff"] = ot_group.sort_values(by='Total Overtime Hours', ascending=False).head(5)

    # 2. Average Working Hours & Shift Pair validation (Optimized Aggregation)
    working_hours = []
    break_durations = []
    abnormal_shifts = []
    behavior_profiles = []

    # Separate event types for aggregation
    df_ins = df_merged[(df_merged['type'] == 'CLOCK_IN') & (~df_merged['status'].isin(['End Break', 'Start Break']))]
    df_outs = df_merged[(df_merged['type'] == 'CLOCK_OUT') & (~df_merged['status'].isin(['Start Break', 'End Break']))]
    df_start_breaks = df_merged[df_merged['status'] == 'Start Break']
    df_end_breaks = df_merged[df_merged['status'] == 'End Break']

    # Aggregate Clock-ins
    df_ins_sorted = df_ins.sort_values('dt')
    ins_agg = df_ins_sorted.groupby(['date_dt', 'NRP']).agg(
        in_t=('dt', 'min'),
        in_time_str=('time', 'min'),
        in_status=('status', 'first'),
        employee_name=('employee_name', 'first'),
        work_end=('work_end', 'first')
    ).reset_index()

    # Aggregate Clock-outs
    df_outs_sorted = df_outs.sort_values('dt')
    outs_agg = df_outs_sorted.groupby(['date_dt', 'NRP']).agg(
        out_t=('dt', 'max'),
        out_time_str=('time', 'max'),
        out_status=('status', 'last')
    ).reset_index()

    # Aggregate Breaks
    sb_agg = df_start_breaks.groupby(['date_dt', 'NRP']).agg(sb_t=('dt', 'min')).reset_index()
    eb_agg = df_end_breaks.groupby(['date_dt', 'NRP']).agg(eb_t=('dt', 'max')).reset_index()

    # Group all (date, NRP) combinations
    all_pairs = df_merged.groupby(['date_dt', 'NRP']).agg(
        employee_name=('employee_name', 'first'),
        work_start=('work_start', 'first'),
        work_end=('work_end', 'first'),
        Outlet=('Outlet', 'first')
    ).reset_index()

    # Merge everything
    all_shifts = all_pairs.merge(ins_agg, on=['date_dt', 'NRP'], how='left', suffixes=('', '_ins'))
    all_shifts = all_shifts.merge(outs_agg, on=['date_dt', 'NRP'], how='left')
    all_shifts = all_shifts.merge(sb_agg, on=['date_dt', 'NRP'], how='left')
    all_shifts = all_shifts.merge(eb_agg, on=['date_dt', 'NRP'], how='left')

    # Vectorized shift duration & break calculations
    all_shifts['diff_h'] = (all_shifts['out_t'] - all_shifts['in_t']).dt.total_seconds() / 3600.0
    all_shifts['break_m'] = (all_shifts['eb_t'] - all_shifts['sb_t']).dt.total_seconds() / 60.0
    all_shifts['break_m'] = all_shifts['break_m'].fillna(0.0)

    # Collect lists of working/break durations
    working_hours = all_shifts[all_shifts['diff_h'].between(0.0001, 24.0)]['diff_h'].tolist()
    break_durations = all_shifts[all_shifts['break_m'].between(0.0001, 180.0)]['break_m'].tolist()

    # Iterate over flat records for classification (Extremely fast compared to original Pandas loop)
    today = datetime.now().date()
    now_time_str = datetime.now().strftime('%H:%M')
    now_mins = parse_to_minutes(now_time_str)

    # Dictionary for fast NRP to Name resolution
    name_col = 'Nama Staff' if 'Nama Staff' in df_employees.columns else ('name' if 'name' in df_employees.columns else 'employee_name')
    nrp_to_name = dict(zip(df_employees['NRP'].astype(str), df_employees[name_col]))

    # Build employee schedule expected hours mapping
    # Build employee default expected hours mapping from master sheet's work_start and work_end
    emp_expected_hours = {}
    if not df_employees.empty:
        for row_emp in df_employees.to_dict('records'):
            emp_nrp = str(row_emp.get('NRP', ''))
            ws = row_emp.get('work_start', '')
            we = row_emp.get('work_end', '')
            hours = 0.0
            if ws and we:
                s_min = parse_to_minutes(str(ws))
                e_min = parse_to_minutes(str(we))
                if s_min is not None and e_min is not None:
                    if e_min > s_min:
                        hours = (e_min - s_min) / 60.0
                    else:
                        hours = ((1440 - s_min) + e_min) / 60.0
            emp_expected_hours[emp_nrp] = hours

    for row in all_shifts.to_dict('records'):
        date_val = row['date_dt']
        date_str = date_val.strftime('%Y-%m-%d')
        nrp = row['NRP']
        emp_name = row['employee_name'] or 'Unknown'

        in_t = row['in_t']
        out_t = row['out_t']
        in_time_str = row['in_time_str'] or 'N/A'
        out_time_str = row['out_time_str'] or 'N/A'
        in_status = row['in_status'] or 'On Time'
        out_status = row['out_status'] or 'On Time'
        break_m = row['break_m']
        diff_h = row['diff_h']
        work_start_val = row.get('work_start')
        work_end_val = row.get('work_end')

        # 1. Abnormal shift checks
        if pd.notna(in_t) and pd.notna(out_t):
            if 0 < diff_h <= 24.0:
                if diff_h < 6.0:
                    abnormal_shifts.append({
                        "Date": date_str,
                        "NRP": nrp,
                        "Name": emp_name,
                        "Duration": f"{diff_h:.2f}h",
                        "Details": f"Short Shift ({diff_h:.2f}h) - Clock-in: {in_time_str}, Clock-out: {out_time_str}"
                    })
            else:
                abnormal_shifts.append({
                    "Date": date_str,
                    "NRP": nrp,
                    "Name": emp_name,
                    "Duration": "N/A",
                    "Details": f"Abnormal shift length ({diff_h:.1f}h)"
                })
        elif pd.notna(in_t):
            is_today = (date_val == today)
            is_abnormal = True
            if is_today:
                if work_end_val and str(work_end_val).strip():
                    t_end_mins = parse_to_minutes(str(work_end_val))
                    if now_mins is not None and t_end_mins is not None and now_mins < t_end_mins:
                        is_abnormal = False
                else:
                    is_abnormal = False
            if is_abnormal:
                abnormal_shifts.append({
                    "Date": date_str,
                    "NRP": nrp,
                    "Name": emp_name,
                    "Duration": "N/A",
                    "Details": f"Missing Clock Out (Clocked in at {in_time_str})"
                })
        elif pd.notna(out_t):
            abnormal_shifts.append({
                "Date": date_str,
                "NRP": nrp,
                "Name": emp_name,
                "Duration": "N/A",
                "Details": f"Missing Clock In (Clocked out at {out_time_str})"
            })

        # 2. Daily Attendance Pattern mapping
        pattern = "Standard"
        if pd.isna(in_t) and pd.notna(out_t):
            pattern = "Missing Clock In"
        elif pd.notna(in_t) and pd.isna(out_t):
            is_today = (date_val == today)
            is_active_shift = False
            if is_today:
                if work_end_val and str(work_end_val).strip():
                    t_end_mins = parse_to_minutes(str(work_end_val))
                    if now_mins is not None and t_end_mins is not None and now_mins < t_end_mins:
                        is_active_shift = True
                else:
                    is_active_shift = True
            if is_active_shift:
                pattern = "Currently Working"
            else:
                pattern = "Missing Clock Out"
        elif pd.notna(in_t) and pd.na(out_t) if False else (pd.notna(in_t) and pd.notna(out_t)): # Check safe
            pattern = f"{in_status} / {out_status}"
        else:
            pattern = "Anomalous Scan Pattern"

        # Calculate expected hours for this active day
        expected_h = emp_expected_hours.get(nrp, 0.0)
        if expected_h == 0.0 and work_start_val and work_end_val:
            s_min = parse_to_minutes(str(work_start_val))
            e_min = parse_to_minutes(str(work_end_val))
            if s_min is not None and e_min is not None:
                if e_min > s_min:
                    expected_h = (e_min - s_min) / 60.0
                else:
                    expected_h = ((1440 - s_min) + e_min) / 60.0

        behavior_profiles.append({
            "Date": date_str,
            "NRP": nrp,
            "Name": emp_name,
            "Pattern": pattern,
            "Clock In": in_time_str if in_time_str else "N/A",
            "Clock Out": out_time_str if out_time_str else "N/A",
            "Break (mins)": f"{break_m:.0f}" if (pd.notna(break_m) and break_m > 0) else "0",
            "Work Hours": f"{diff_h:.1f}h" if (pd.notna(diff_h) and diff_h > 0) else "0h",
            "Expected Hours": expected_h
        })

    # Detect absences (optimized Cartesian product difference)
    if not df_emp_filtered.empty and not df_merged.empty:
        all_nrps = list(df_emp_filtered['NRP'].astype(str).tolist())
        unique_dates = df_merged['date_dt'].unique()
        
        # All possible (date, NRP) combinations
        all_combinations = [(d, n) for d in unique_dates for n in all_nrps]
        
        # Scanned combinations
        scanned_combinations = set(zip(df_merged['date_dt'], df_merged['NRP'].astype(str)))
        
        # Absent combinations
        absent_combinations = [c for c in all_combinations if c not in scanned_combinations]
        
        for date_val, nrp in absent_combinations:
            date_str = date_val.strftime('%Y-%m-%d')
            emp_name = nrp_to_name.get(nrp, 'Unknown')
            
            # Calculate expected hours for this absent day
            expected_h = emp_expected_hours.get(nrp, 0.0)
                
            behavior_profiles.append({
                "Date": date_str,
                "NRP": nrp,
                "Name": emp_name,
                "Pattern": "Absent (No-Show)",
                "Clock In": "N/A",
                "Clock Out": "N/A",
                "Break (mins)": "0",
                "Work Hours": "0h",
                "Expected Hours": expected_h
            })

    df_profiles = pd.DataFrame(behavior_profiles)
    
    # Pre-calculate consolidated profiles for all employees matching the current Outlet/AM/Position filters.
    # Map outlet to HK (Hari Kerja) from df_outlets
    outlet_hk_map = {}
    if df_outlets is not None and not df_outlets.empty and 'Outlet' in df_outlets.columns:
        hk_col = 'HK' if 'HK' in df_outlets.columns else None
        if hk_col:
            for _, r in df_outlets.iterrows():
                o_name = str(r.get('Outlet', '')).strip()
                try:
                    val = r.get(hk_col)
                    hk_val = float(val) if pd.notna(val) and str(val).strip() not in ['', 'nan', 'None'] else 22.0
                except (ValueError, TypeError):
                    hk_val = 22.0
                if o_name:
                    outlet_hk_map[o_name] = hk_val

    emp_hk_map = {}
    if not df_emp_filtered.empty and 'NRP' in df_emp_filtered.columns:
        out_col = 'Outlet' if 'Outlet' in df_emp_filtered.columns else None
        for _, r in df_emp_filtered.iterrows():
            nrp_val = str(r.get('NRP', '')).strip()
            o_val = str(r.get(out_col, '')).strip() if out_col else ''
            emp_hk_map[nrp_val] = outlet_hk_map.get(o_val, 22.0)

    # This is used for rendering the overall behavior distribution (Pie/Bubble charts) and is returned unfiltered by profile type.
    df_consolidated = pd.DataFrame()
    if not df_profiles.empty:
        consolidated_records = []
        for (nrp, name), group in df_profiles.groupby(['NRP', 'Name']):
            emp_hk = emp_hk_map.get(str(nrp).strip(), 22.0)
            consolidated_profile = aggregate_employee_profile(group, hk=emp_hk)
            
            # Count total active days vs absent days in this period
            total_days = len(group)
            absent_days = len(group[group['Pattern'] == "Absent (No-Show)"])
            active_days = total_days - absent_days
            
            # Count missing scans in this period
            missing_in = len(group[group['Pattern'] == "Missing Clock In"])
            missing_out = len(group[group['Pattern'] == "Missing Clock Out"])
            
            # Calculate total working hours in this period
            total_working_hours = 0.0
            if 'Work Hours' in group.columns:
                try:
                    hours_series = group['Work Hours'].astype(str).str.replace('h', '', regex=False)
                    hours_float = pd.to_numeric(hours_series, errors='coerce').fillna(0.0)
                    total_working_hours = float(hours_float.sum())
                except Exception:
                    pass
            
            # Calculate total expected working hours in this period
            total_expected_hours = 0.0
            if 'Expected Hours' in group.columns:
                try:
                    total_expected_hours = float(group['Expected Hours'].sum())
                except Exception:
                    pass
            
            consolidated_records.append({
                "NRP": nrp,
                "Name": name,
                "Consolidated Profile": consolidated_profile,
                "Active Days": active_days,
                "Absent Days": absent_days,
                "Missing Scans": missing_in + missing_out,
                "HK (Hari Kerja)": int(emp_hk),
                "Total Working Hours": round(total_working_hours, 1),
                "Expected Working Hours": round(total_expected_hours, 1)
            })
        df_consolidated = pd.DataFrame(consolidated_records)
        
    analytics["df_consolidated_unfiltered"] = df_consolidated
    analytics["behavior_profiles_unfiltered"] = df_profiles
    
    # Handle multiselect list/tuple/set/str for selected_profile_type
    if isinstance(selected_profile_type, (list, tuple, set)):
        selected_profiles_list = [str(x).strip() for x in selected_profile_type if str(x).strip() and str(x).strip() != "All"]
    elif isinstance(selected_profile_type, str) and selected_profile_type.strip() != "All" and selected_profile_type.strip() != "":
        selected_profiles_list = [selected_profile_type.strip()]
    else:
        selected_profiles_list = []

    # Apply Behavior Profile Category filter to the rest of the metrics if specific profile(s) are selected!
    if selected_profiles_list:
        if not df_consolidated.empty:
            matching_nrps = df_consolidated[df_consolidated['Consolidated Profile'].isin(selected_profiles_list)]['NRP'].tolist()
        else:
            matching_nrps = []
            
        df_emp_filtered = df_emp_filtered[df_emp_filtered['NRP'].isin(matching_nrps)]
        df_merged = df_merged[df_merged['NRP'].isin(matching_nrps)]
        if not df_profiles.empty:
            df_profiles = df_profiles[df_profiles['NRP'].isin(matching_nrps)]
        all_shifts = all_shifts[all_shifts['NRP'].isin(matching_nrps)]
        abnormal_shifts = [s for s in abnormal_shifts if s['NRP'] in matching_nrps]
        
        # Recalculate working_hours and break_durations from filtered all_shifts
        working_hours = all_shifts[all_shifts['diff_h'].between(0.0001, 24.0)]['diff_h'].tolist()
        break_durations = all_shifts[all_shifts['break_m'].between(0.0001, 180.0)]['break_m'].tolist()
        
        # Recalculate clock_ins and clock_outs based on filtered df_merged
        clock_ins = df_merged[(df_merged['type'] == 'CLOCK_IN') & (~df_merged['status'].isin(['End Break', 'Start Break']))]
        clock_outs = df_merged[(df_merged['type'] == 'CLOCK_OUT') & (~df_merged['status'].isin(['Start Break', 'End Break']))]
        
        analytics["total_scans"] = len(df_merged)
        analytics["total_active_staff"] = len(df_emp_filtered)

    analytics["behavior_profiles"] = df_profiles
    analytics["abnormal_shifts"] = abnormal_shifts

    # 3. Daily trends (Headcount and Scans)
    daily_scans_count = df_merged.groupby('date_dt').size().reset_index(name='Total Scans')
    daily_work_starts = df_merged[(df_merged['type'] == 'CLOCK_IN') & (~df_merged['status'].isin(['End Break', 'Start Break']))]
    daily_headcount = daily_work_starts.groupby('date_dt')['NRP'].nunique().reset_index(name='Employees Present')

    daily_gp = pd.merge(daily_scans_count, daily_headcount, on='date_dt', how='left')
    daily_gp['Employees Present'] = daily_gp['Employees Present'].fillna(0).astype(int)
    daily_gp = daily_gp.rename(columns={'date_dt': 'date'})
    analytics["daily_scans"] = daily_gp.sort_values('date')
    if not daily_gp.empty:
        analytics["avg_daily_headcount"] = daily_gp['Employees Present'].mean()

    # 4. Hourly distribution (Optimized Vectorized scan type assignments)
    if not df_merged.empty:
        df_temp = df_merged.copy()
        df_temp['hour'] = df_temp['dt'].dt.hour

        # Vectorized label assignment
        df_temp['Scan Type'] = 'Other'
        df_temp.loc[df_temp['status'] == 'Start Break', 'Scan Type'] = 'Start Break'
        df_temp.loc[df_temp['status'] == 'End Break', 'Scan Type'] = 'End Break'
        df_temp.loc[(df_temp['type'] == 'CLOCK_IN') & (df_temp['Scan Type'] == 'Other'), 'Scan Type'] = 'Clock-in'
        df_temp.loc[(df_temp['type'] == 'CLOCK_OUT') & (df_temp['Scan Type'] == 'Other'), 'Scan Type'] = 'Clock-out'
        
        df_temp = df_temp[df_temp['Scan Type'] != 'Other']

        scan_types = ['Clock-in', 'Clock-out', 'Start Break', 'End Break']
        base_records = []
        for h in range(24):
            for st_type in scan_types:
                base_records.append({'hour': h, 'Scan Type': st_type, 'Scans': 0})
        df_base = pd.DataFrame(base_records)

        if not df_temp.empty:
            hourly_gp = df_temp.groupby(['hour', 'Scan Type']).size().reset_index(name='Scans_actual')
            df_merged_gp = pd.merge(df_base, hourly_gp, on=['hour', 'Scan Type'], how='left')
            df_merged_gp['Scans'] = df_merged_gp['Scans_actual'].fillna(0).astype(int)
            df_merged_gp = df_merged_gp.drop(columns=['Scans_actual'])
        else:
            df_merged_gp = df_base

        df_merged_gp['Hour Interval'] = df_merged_gp['hour'].apply(lambda h: f"{h:02d}:00 - {h+1:02d}:00")
        analytics["hourly_distribution"] = df_merged_gp.sort_values(['hour', 'Scan Type'])

    # 5. Status distribution
    if not clock_ins.empty:
        analytics["status_distribution"] = clock_ins.groupby('status').size().reset_index(name='Count')
    if not clock_outs.empty:
        analytics["checkout_status_distribution"] = clock_outs.groupby('status').size().reset_index(name='Count')

    # 5.b SVP Approval Status distribution (for records requiring supervisor approval)
    if not df_merged.empty and "notes" in df_merged.columns:
        has_svp_note = df_merged['notes'].astype(str).str.contains('Supervisor Approval Required|APPROVED by SPV|REJECTED by SPV', case=False, na=False)
        has_svp_cat = df_merged['Case Category'].isin(['Lupa Absen', 'Izin Terlambat', 'Pulang Awal', 'Tugas Luar', 'Supervisor Approval']) if 'Case Category' in df_merged.columns else False
        df_svp_req = df_merged[has_svp_note | has_svp_cat].copy()

        if not df_svp_req.empty:
            def resolve_case_category(row):
                cc = str(row.get('Case Category', '')).strip()
                if cc and cc not in ['', 'nan', 'None', 'NaN']:
                    return cc
                notes = str(row.get('notes', ''))
                if 'Exceeded Monthly HK' in notes or 'Exceeded HK' in notes:
                    return 'Melebihi HK Normal'
                elif 'Belanja' in notes or 'Belanja Kebutuhan Outlet' in notes:
                    return 'Belanja Kebutuhan Outlet'
                elif 'Izin Terlambat' in notes:
                    return 'Izin Terlambat'
                elif 'Pulang Awal' in notes:
                    return 'Pulang Awal'
                elif 'Lupa Absen' in notes:
                    return 'Lupa Absen'
                return 'Perizinan Absen'

            df_svp_req['Case Category'] = df_svp_req.apply(resolve_case_category, axis=1)

            if 'Review Status' not in df_svp_req.columns:
                df_svp_req['Review Status'] = 'Waiting for Supervisor approval'
            else:
                df_svp_req['Review Status'] = df_svp_req['Review Status'].fillna('Waiting for Supervisor approval')
                df_svp_req.loc[df_svp_req['Review Status'].astype(str).str.strip().isin(['', 'none', 'nan', 'None', 'NaN']), 'Review Status'] = 'Waiting for Supervisor approval'

            group_cols = ['Case Category', 'Review Status']
            analytics["svp_approval_distribution"] = df_svp_req.groupby(group_cols).size().reset_index(name='Count')
            
            # Compute detailed table (NRP, Name, Case Category, Review Status, count)
            name_col = 'Nama Staff' if 'Nama Staff' in df_svp_req.columns else ('employee_name' if 'employee_name' in df_svp_req.columns else 'NRP')
            cc_col = 'Case Category' if 'Case Category' in df_svp_req.columns else None
            
            tbl_cols = ['NRP', name_col]
            if cc_col:
                tbl_cols.append(cc_col)
            tbl_cols.append('Review Status')
            
            df_tbl = df_svp_req.groupby(tbl_cols).size().reset_index(name='Total Requests')
            df_tbl.rename(columns={name_col: 'Nama Staff', 'NRP': 'Employee NRP'}, inplace=True)
            analytics["svp_approval_table"] = df_tbl
        else:
            analytics["svp_approval_distribution"] = pd.DataFrame(columns=['Case Category', 'Review Status', 'Count'])
            analytics["svp_approval_table"] = pd.DataFrame(columns=['Employee NRP', 'Nama Staff', 'Case Category', 'Review Status', 'Total Requests'])
    else:
        analytics["svp_approval_distribution"] = pd.DataFrame(columns=['Case Category', 'Review Status', 'Count'])
        analytics["svp_approval_table"] = pd.DataFrame(columns=['Employee NRP', 'Nama Staff', 'Case Category', 'Review Status', 'Total Requests'])

    # 6. Outlet performance metrics (Using pre-computed shifts)
    avg_hours_by_outlet = all_shifts[all_shifts['diff_h'].between(0.0001, 24.0)].groupby('Outlet')['diff_h'].mean().to_dict()

    outlet_performance = []
    for o_name, o_gp in df_merged.groupby('Outlet'):
        o_ins = o_gp[(o_gp['type'] == 'CLOCK_IN') & (~o_gp['status'].isin(['End Break', 'Start Break']))]
        o_lates = o_ins[o_ins['status'] == 'Late In']
        o_late_rate = (len(o_lates) / len(o_ins)) * 100 if not o_ins.empty else 0.0

        o_outs = o_gp[(o_gp['type'] == 'CLOCK_OUT') & (~o_gp['status'].isin(['Start Break', 'End Break']))]
        o_early_outs = o_outs[o_outs['status'] == 'Early Out']
        o_early_out_rate = (len(o_early_outs) / len(o_outs)) * 100 if not o_outs.empty else 0.0

        o_overtimes = o_outs[o_outs['status'] == 'Over Time']
        o_overtime_rate = (len(o_overtimes) / len(o_outs)) * 100 if not o_outs.empty else 0.0

        o_avg_wh = avg_hours_by_outlet.get(o_name, 0.0)

        outlet_performance.append({
            "Outlet": o_name,
            "Total Scans": len(o_gp),
            "Late Rate (%)": round(o_late_rate, 1),
            "Early Out Rate (%)": round(o_early_out_rate, 1),
            "Overtime Rate (%)": round(o_overtime_rate, 1),
            "Avg Hours": round(o_avg_wh, 2)
        })
    if outlet_performance:
        analytics["outlet_performance"] = pd.DataFrame(outlet_performance)

    # 7. Working employee heatmap (Optimized Linear scan algorithm)
    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    unique_dates = df_merged['date_dt'].unique()
    heatmap_sums = {day: {h: {} for h in range(24)} for day in days_of_week}

    # Initialize all dates to 0 working headcount
    for date_val in unique_dates:
        day_name = date_val.strftime('%A')
        if day_name in days_of_week:
            for h in range(24):
                heatmap_sums[day_name][h][date_val] = 0

    from collections import defaultdict
    df_sorted = df_merged.sort_values('dt')
    
    # Pre-group scans in pure Python to avoid massive Pandas groupby overhead
    grouped_scans = defaultdict(list)
    for r in df_sorted.to_dict('records'):
        grouped_scans[(r['date_dt'], r['NRP'])].append(r)

    for (date_val, nrp), scans_list in grouped_scans.items():
        day_name = date_val.strftime('%A')
        if day_name not in days_of_week:
            continue

        scans = []
        for r in scans_list:
            dt_val = r['dt']
            mins = dt_val.hour * 60 + dt_val.minute
            t = r['type']
            s = r['status']

            is_working = False
            if dt_val <= datetime.now():
                if t == 'CLOCK_IN':
                    if s not in ['Start Break']:
                        is_working = True
                elif t == 'CLOCK_OUT':
                    is_working = False

                if s == 'Start Break':
                    is_working = False
                elif s == 'End Break':
                    is_working = True
            scans.append((mins, is_working))

        # Linear scan pointer evaluation across 24 hours
        scan_idx = 0
        num_scans = len(scans)
        current_state = False

        for h in range(24):
            sample_mins = h * 60 + 30
            while scan_idx < num_scans and scans[scan_idx][0] <= sample_mins:
                current_state = scans[scan_idx][1]
                scan_idx += 1
            if current_state:
                heatmap_sums[day_name][h][date_val] += 1

    pivot_data = []
    for day in days_of_week:
        row_data = {}
        for h in range(24):
            date_counts = heatmap_sums[day][h]
            if not date_counts:
                row_data[h] = 0.0
            else:
                row_data[h] = sum(date_counts.values()) / len(date_counts)
        pivot_data.append(row_data)

    analytics["heatmap_data"] = pd.DataFrame(pivot_data, index=days_of_week, columns=range(24))

    # 8. Overtime Analytics
    total_ot_hours = df_merged['overtime_hours'].sum() if 'overtime_hours' in df_merged.columns else 0.0
    num_days = df_merged['date_dt'].nunique()
    analytics["avg_daily_overtime"] = total_ot_hours / num_days if num_days > 0 else 0.0

    # 9. Distance & GPS Accuracy Metrics
    dist_series = df_merged['distance_meters'] if 'distance_meters' in df_merged.columns else pd.Series()
    acc_series = df_merged['gps_accuracy'] if 'gps_accuracy' in df_merged.columns else pd.Series()
    
    dist_nums = pd.to_numeric(dist_series.astype(str).str.extract(r'(\d+)')[0], errors='coerce')
    acc_nums = pd.to_numeric(acc_series.astype(str).str.extract(r'(\d+)')[0], errors='coerce')
    
    valid_dist = dist_nums.dropna()
    valid_acc = acc_nums.dropna()
    
    analytics["avg_distance_meters"] = float(valid_dist.mean()) if not valid_dist.empty else 0.0
    analytics["max_distance_meters"] = float(valid_dist.max()) if not valid_dist.empty else 0.0
    analytics["out_of_radius_count"] = int((valid_dist > 50).sum())
    analytics["out_of_radius_rate"] = (float(analytics["out_of_radius_count"]) / len(df_merged) * 100.0) if len(df_merged) > 0 else 0.0

    analytics["avg_gps_accuracy"] = float(valid_acc.mean()) if not valid_acc.empty else 0.0
    high_acc = valid_acc[valid_acc <= 50]
    analytics["high_accuracy_rate"] = (len(high_acc) / len(valid_acc) * 100.0) if not valid_acc.empty else 0.0

    df_merged['_dist_num'] = dist_nums
    df_merged['_acc_num'] = acc_nums
    analytics["raw_merged_df"] = df_merged.copy()

    if 'Outlet' in df_merged.columns:
        outlet_dist_acc = df_merged.groupby('Outlet').agg(
            Avg_Distance=('_dist_num', 'mean'),
            Avg_Accuracy=('_acc_num', 'mean'),
            Total_Scans=('NRP', 'count')
        ).reset_index()
        outlet_dist_acc['Avg_Distance'] = outlet_dist_acc['Avg_Distance'].round(1)
        outlet_dist_acc['Avg_Accuracy'] = outlet_dist_acc['Avg_Accuracy'].round(1)
        analytics["outlet_distance_accuracy"] = outlet_dist_acc
    else:
        analytics["outlet_distance_accuracy"] = pd.DataFrame(columns=['Outlet', 'Avg_Distance', 'Avg_Accuracy', 'Total_Scans'])

    audit_scans = df_merged[(df_merged['_dist_num'] > 50) | (df_merged['_acc_num'] > 50)].copy()
    if not audit_scans.empty:
        audit_cols = ['NRP', 'employee_name', 'timestamp', 'Outlet', 'type', 'distance_meters', 'gps_accuracy', 'notes']
        audit_existing = [c for c in audit_cols if c in audit_scans.columns]
        analytics["audit_location_scans"] = audit_scans[audit_existing].sort_values('timestamp', ascending=False)
    else:
        analytics["audit_location_scans"] = pd.DataFrame()

    return analytics

def main():
    icon_path = os.path.join(PROJECT_ROOT, "img", "HR_Portal.ico")
    try:
        from PIL import Image
        icon = Image.open(icon_path)
    except Exception:
        icon = "💼"
        
    st.set_page_config(
        page_title="HR Admin Portal",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    
    # Check if shutdown was triggered
    if st.session_state.get("shutdown_triggered", False):
        st.markdown(
            """
            <div style="text-align: center; margin-top: 100px; padding: 40px; background: rgba(255, 255, 255, 0.75); border-radius: 12px; border: 1px solid rgba(15, 23, 42, 0.08); box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04); font-family: 'Outfit', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Roboto', 'Helvetica', 'Arial', sans-serif;">
                <h1 style="color: #dc2626; font-size: 2.2rem; font-weight: 700; margin-bottom: 16px;">🔌 Terminated</h1>
                <p style="color: #475569; font-size: 1.1rem; margin-bottom: 24px;">The HR Admin Portal has been shut down successfully.</p>
                <div style="display: inline-block; background: rgba(15, 23, 42, 0.03); border: 1px solid rgba(15, 23, 42, 0.06); padding: 8px 16px; border-radius: 50px; color: #475569; font-weight: 500; font-size: 0.95rem;">
                    💡 You can now safely close this browser tab.
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        time.sleep(1.0)
        os._exit(0)
    
    import base64
    icon_html = "💼 "
    if os.path.exists(icon_path):
        try:
            with open(icon_path, "rb") as f:
                icon_base64 = base64.b64encode(f.read()).decode("utf-8")
            icon_html = f'<img src="data:image/x-icon;base64,{icon_base64}" width="36" style="vertical-align: middle; margin-right: 12px; margin-bottom: 6px;">'
        except Exception:
            pass

    # Load retention settings from JSON
    settings_file = os.path.join(PROJECT_ROOT, "data", "retention_settings.json")
    default_enabled = True
    default_months = 12
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r") as f:
                data = json.load(f)
                default_enabled = data.get("retention_enabled", True)
                default_months = data.get("retention_months", 12)
        except Exception:
            pass

    # Set default session states
    if "retention_enabled" not in st.session_state:
        st.session_state.retention_enabled = default_enabled
    if "retention_months" not in st.session_state:
        st.session_state.retention_months = default_months

    def on_retention_change():
        enabled = st.session_state.get("retention_enabled", True)
        months = st.session_state.get("retention_months", 12)
        try:
            os.makedirs(os.path.dirname(settings_file), exist_ok=True)
            existing_data = {}
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, "r") as f:
                        existing_data = json.load(f)
                except Exception:
                    pass
            existing_data["retention_enabled"] = enabled
            existing_data["retention_months"] = months
            with open(settings_file, "w") as f:
                json.dump(existing_data, f, indent=4)
        except Exception:
            pass
    # Load Environment variables
    hr_db_path = os.getenv("HR_ADMIN_DB_PATH", "data/hr_system.duckdb")
    if not os.path.isabs(hr_db_path):
        hr_db_file = os.path.join(PROJECT_ROOT, hr_db_path)
    else:
        hr_db_file = hr_db_path
        
    # emp_db_path is not used here anymore to prevent using attendance_system.duckdb

    # Initialize DuckDB database handlers
    db_handler = DatabaseHandler(hr_db_file)
    db_handler.init_admin_credentials()
    emp_db_handler = DatabaseHandler(hr_db_file)

    # Inject CSS for styled containers & metrics
    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');

            .block-container {
                padding-top: 2rem;
                padding-bottom: 2rem;
            }
            
            /* General font styling */
            html, body, [class*="css"] {
                font-family: 'Outfit', sans-serif;
            }
            
            /* Custom styling for primary login button (avoiding eye icon button) */
            div[data-testid="stFormSubmitButton"] button {
                background-color: #3573bf !important;
                color: white !important;
                border: none !important;
                border-radius: 6px !important;
                padding: 10px 24px !important;
                font-family: 'Outfit', sans-serif !important;
                font-weight: 500 !important;
                font-size: 1rem !important;
                transition: background-color 0.2s ease, transform 0.1s ease !important;
                width: 100% !important;
                cursor: pointer;
            }
            div[data-testid="stFormSubmitButton"] button:hover {
                background-color: #295b9a !important;
                transform: translateY(-1px) !important;
            }
            div[data-testid="stFormSubmitButton"] button:active {
                transform: translateY(0px) !important;
            }
            
            
            /* Premium Dashboard Metric Cards */
            .stat-card {
                background: var(--secondary-background-color, #F8FAFC);
                border: 1px solid rgba(128, 128, 128, 0.1);
                border-left: 4px solid #3B82F6;
                border-radius: 6px;
                padding: 8px 10px;
                box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
                margin-bottom: 4px;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
                min-height: 86px;
                height: 100%;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
            }
            .stat-card:hover {
                transform: translateY(-1px);
                box-shadow: 0 3px 8px rgba(0, 0, 0, 0.06);
            }
            .stat-card h4 {
                margin: 0;
                color: var(--text-color, #4B5563);
                opacity: 0.8;
                font-size: 0.70rem;
                line-height: 1.25;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                font-weight: 600;
                min-height: 2.5em;
                display: flex;
                align-items: flex-start;
            }
            .stat-card p {
                margin: 4px 0 0 0;
                color: var(--text-color, #1F2937);
                font-size: 1.45rem;
                line-height: 1.1;
                font-weight: 700;
                font-family: 'Outfit', sans-serif;
            }
            .stat-card.card-blue { border-left-color: #3573bf; }
            .stat-card.card-purple { border-left-color: #8B5CF6; }
            .stat-card.card-emerald { border-left-color: #10B981; }
            .stat-card.card-amber { border-left-color: #F59E0B; }
            .stat-card.card-rose { border-left-color: #F43F5E; }

            

            /* Hide Streamlit sidebar completely */
            [data-testid="stSidebar"] {
                display: none !important;
            }
            /* Hide the sidebar toggle button at the top-left */
            [data-testid="collapsedControl"] {
                display: none !important;
            }
            /* Hide Streamlit header (Deploy button, settings menu) */
            header {
                visibility: hidden;
                height: 0px;
            }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Helper to check auth parameter to keep logged in across page refreshes
    try:
        auth_param = st.query_params.get("auth", "")
    except AttributeError:
        try:
            auth_param = st.experimental_get_query_params().get("auth", [""])[0]
        except Exception:
            auth_param = ""

    # Initialize session state for authentication & transition phase
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = (auth_param == "admin")
    if "loading_transition" not in st.session_state:
        st.session_state.loading_transition = False
        
    # Initialize profile builder rules thresholds from persistent JSON
    thresholds = load_profile_thresholds()
    if "p_absent_thresh" not in st.session_state:
        st.session_state.p_absent_thresh = thresholds["p_absent_thresh"]
    if "p_missing_thresh" not in st.session_state:
        st.session_state.p_missing_thresh = thresholds["p_missing_thresh"]
    if "p_slipping_thresh1" not in st.session_state:
        st.session_state.p_slipping_thresh1 = thresholds["p_slipping_thresh1"]
    if "p_slipping_thresh2" not in st.session_state:
        st.session_state.p_slipping_thresh2 = thresholds["p_slipping_thresh2"]
    if "p_compensatory_thresh" not in st.session_state:
        st.session_state.p_compensatory_thresh = thresholds["p_compensatory_thresh"]
    if "p_late_thresh" not in st.session_state:
        st.session_state.p_late_thresh = thresholds["p_late_thresh"]
    if "p_early_thresh" not in st.session_state:
        st.session_state.p_early_thresh = thresholds["p_early_thresh"]
    if "p_overtime_thresh" not in st.session_state:
        st.session_state.p_overtime_thresh = thresholds["p_overtime_thresh"]
    if "p_break_thresh" not in st.session_state:
        st.session_state.p_break_thresh = thresholds["p_break_thresh"]
    if "p_break_mins" not in st.session_state:
        st.session_state.p_break_mins = thresholds["p_break_mins"]
    if "early_in_thresh_min" not in st.session_state:
        st.session_state.early_in_thresh_min = thresholds.get("early_in_thresh_min", 10)
    if "clock_in_grace_min" not in st.session_state:
        st.session_state.clock_in_grace_min = thresholds.get("clock_in_grace_min", 10)
    if "clock_out_grace_min" not in st.session_state:
        st.session_state.clock_out_grace_min = thresholds.get("clock_out_grace_min", 10)
    if "late_out_thresh_min" not in st.session_state:
        st.session_state.late_out_thresh_min = thresholds.get("late_out_thresh_min", 120)


    # 1. Login Gate (Phase 1)
    if not st.session_state.admin_authenticated:
        with st.container():
            col1, col2, col3 = st.columns([1.5, 1.2, 1.5])
            with col2:
                st.markdown("<div style='height: 80px;'></div>", unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div style="text-align: center; margin-bottom: 24px;">
                        <div style="font-size: 2rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; gap: 8px; width: 100%;">
                            {icon_html}HR Admin Portal
                        </div>
                        <div style="font-size: 0.95rem; opacity: 0.7; margin-top: 6px;">
                            Sign in to manage employees, attendance logs, and system settings
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                with st.form("login_form"):
                    username = st.text_input("Username")
                    password = st.text_input("Password", type="password")
                    submit = st.form_submit_button("Sign In", use_container_width=False)
                    
                    if submit:
                        if db_handler.authenticate_admin(username, password):
                            st.session_state.admin_authenticated = True
                            st.session_state.loading_transition = True  # Enable loading view for next run
                            st.rerun()  # Rerun immediately to swap login screen with loading transition
                        else:
                            st.error("Invalid username or password. Please try again.")
                
                # Forgot password option
                col_forgot, col_reset = st.columns([2, 1])
                with col_forgot:
                    confirm_reset_login = st.checkbox("🔑 Reset Password", key="confirm_reset_login")
                with col_reset:
                    if confirm_reset_login:
                        if st.button("Reset", use_container_width=False, key="forgot_pwd_btn_login"):
                            if db_handler.reset_admin_password("admin"):
                                st.info("Done.")
                            else:
                                st.error("Failed.")

    # 2. Loading Transition View (Phase 2 - Instant render to clear login screen from browser DOM)
    elif st.session_state.loading_transition:
        st.markdown("<div style='height: 120px;'></div>", unsafe_allow_html=True)
        st.markdown("<h3 style='text-align: center;'>💼 Initializing Administrative Dashboard...</h3>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; opacity: 0.85;'>Loading database cache, staff profiles, and processing behavior analytics...</p>", unsafe_allow_html=True)
        
        # Perform query parameter setup and action logging
        try:
            st.query_params["auth"] = "admin"
        except AttributeError:
            try:
                st.experimental_set_query_params(auth="admin")
            except Exception:
                pass
        db_handler.log_admin_action("LOGIN", "Admin logged in successfully.")
        
        # Purge database based on parameters right after login
        if default_enabled:
            current_month = datetime.now().strftime("%Y-%m")
            last_purge_month = ""
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, "r") as f:
                        s_data = json.load(f)
                        last_purge_month = s_data.get("last_purge_month", "")
                except Exception:
                    pass
            
            if last_purge_month != current_month:
                candidates_count = emp_db_handler.get_purge_candidates_count(default_months)
                if candidates_count > 0:
                    purged_count = emp_db_handler.purge_attendance_records(default_months)
                    db_handler.log_admin_action(
                        "AUTO_PURGE_ON_LOGIN", 
                        f"Auto-purged {purged_count} records older than {default_months} months based on retention policy."
                    )
                
                # Save the last_purge_month to mark it as run for this calendar month
                try:
                    settings_data = {}
                    if os.path.exists(settings_file):
                        with open(settings_file, "r") as f:
                            settings_data = json.load(f)
                    settings_data["last_purge_month"] = current_month
                    with open(settings_file, "w") as f:
                        json.dump(settings_data, f, indent=4)
                except Exception as save_err:
                    logger.error(f"Error saving last_purge_month: {save_err}")
            
        # Turn off transition flag and trigger immediate rerun to load full authenticated dashboard
        st.session_state.loading_transition = False
        st.rerun()

    # 3. Authenticated View (Phase 3)
    else:
        # Header Title and Actions Columns
        col_title, col_sync, col_logout, col_shutdown = st.columns([4.5, 2, 1.2, 0.8])
        with col_title:
            early_in_m = st.session_state.get("early_in_thresh_min", 10)
            clock_in_g = st.session_state.get("clock_in_grace_min", 10)
            clock_out_g = st.session_state.get("clock_out_grace_min", 10)
            late_out_m = st.session_state.get("late_out_thresh_min", 120)
            late_out_h_str = f"{late_out_m // 60} hours" if late_out_m % 60 == 0 else f"{late_out_m / 60.0:.1f} hours"

            help_rules = (
                "### 📋 Scan Status & Timing Rules\n\n"
                "#### 1. Clock-in Scan\n"
                f"* **Early In**: Checked in > {early_in_m} minutes before shift start.\n"
                f"* **On Time**: Checked in within {clock_in_g} minutes prior to shift start.\n"
                "* **Late In**: Checked in after scheduled shift start.\n\n"
                "#### 2. Clock-out Scan\n"
                "* **Early Out**: Clocked out before scheduled shift end.\n"
                f"* **On Time**: Clocked out within the {clock_out_g}-minute grace period after shift end.\n"
                f"* **Late Out**: Clocked out between {clock_out_g} minutes and {late_out_h_str} ({late_out_m} mins) after shift end.\n"
                f"* **Over Time**: Clocked out more than {late_out_h_str} ({late_out_m} mins) after shift end.\n\n"
                "#### 3. Break Scans\n"
                "* **Start Break**: Commenced break.\n"
                "* **End Break**: Concluded break."
            )
            st.header("HR Administrative Dashboard", help=help_rules)

        # Refresh Data button to the left of Log Out
        with col_sync:
            master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
            mp_db_sheet_id = os.getenv("MP_DATABSE_SHEET_ID") or os.getenv("MP_DATABASE_SHEET_ID")
            
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            if st.button("🔄 Refresh Data", type="primary", use_container_width=False):
                with st.spinner("Connecting and transferring records..."):
                    try:
                        credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                        if not os.path.isabs(credentials_val):
                            credentials_file = os.path.join(PROJECT_ROOT, credentials_val)
                        else:
                            credentials_file = credentials_val
                        
                        sheets_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                        
                        from concurrent.futures import ThreadPoolExecutor

                        # 1. Parallelize independent Google Sheets reads
                        with ThreadPoolExecutor(max_workers=6) as executor:
                            fut_emp = executor.submit(
                                sheets_handler.replicate_mp_database, mp_db_sheet_id, master_sheet_id
                            ) if mp_db_sheet_id else executor.submit(
                                sheets_handler.get_master_employee, master_sheet_id
                            )
                            fut_sched = executor.submit(sheets_handler.get_outlet_schedules, master_sheet_id)
                            fut_tpl = executor.submit(sheets_handler.get_shift_templates_data, master_sheet_id)
                            fut_asgn = executor.submit(sheets_handler.get_outlet_template_assignments_data, master_sheet_id)
                            fut_att = executor.submit(sheets_handler.get_attendance_records, master_sheet_id)
                            fut_fp = executor.submit(sheets_handler.get_fingerprint_mappings, master_sheet_id)
                            fut_svp = executor.submit(sheets_handler.get_svp_approval_records, master_sheet_id)

                            df_sheet_emp = fut_emp.result()
                            df_schedules = fut_sched.result()
                            df_tpl_sync = fut_tpl.result()
                            df_asgn_sync = fut_asgn.result()
                            df_sheet_att = fut_att.result()
                            fp_mappings = fut_fp.result()
                            df_sheet_svp = fut_svp.result()

                        # 2. Save Employee & Outlet data
                        synced_outlets_count = 0
                        synced_am_count = 0
                        if not df_sheet_emp.empty:
                            emp_db_handler.save_employees(df_sheet_emp)
                            logger.info(f"✅ Successfully synced and saved {len(df_sheet_emp)} employees from MP Database.")

                            try:
                                synced_am_count = sheets_handler.sync_am_baru_tab(master_sheet_id, df_sheet_emp)
                            except Exception as am_err:
                                logger.error(f"Error syncing AM Baru tab: {am_err}")

                            try:
                                mp_outlets = [str(x).strip() for x in df_sheet_emp['Outlet'].dropna().unique() if str(x).strip() != ""]
                                df_synced_outlets = sheets_handler.sync_outlets_with_mp_database(master_sheet_id, mp_outlets)
                                if not df_synced_outlets.empty:
                                    emp_db_handler.save_outlets(df_synced_outlets)
                                    synced_outlets_count = len(df_synced_outlets)
                                    logger.info(f"✅ Successfully synced {synced_outlets_count} outlets to local database.")
                            except Exception as out_err:
                                logger.error(f"Error syncing outlets with MP Database: {out_err}")
                        else:
                            st.warning("Master employee list was empty or couldn't be loaded.")

                        # 3. Save Schedules and Templates
                        try:
                            if not df_schedules.empty:
                                emp_db_handler.save_outlet_schedules(df_schedules)
                                logger.info(f"✅ Successfully synced {len(df_schedules)} outlet schedule records to local database.")
                        except Exception as sched_err:
                            logger.error(f"Error syncing outlet schedules: {sched_err}")

                        try:
                            if not df_tpl_sync.empty:
                                emp_db_handler.save_shift_templates(df_tpl_sync)
                            if not df_asgn_sync.empty:
                                emp_db_handler.save_outlet_template_assignments(df_asgn_sync)
                        except Exception as tpl_sync_err:
                            logger.error(f"Error syncing shift templates: {tpl_sync_err}")

                        # 4. Save Attendance Logs
                        imported_count = 0
                        cleared = False
                        if not df_sheet_att.empty:
                            imported_count = emp_db_handler.save_attendance_records(df_sheet_att)
                            cleared = sheets_handler.clear_attendance_records(master_sheet_id, keep_days=60)

                        # 5. Save Fingerprint / QR Mappings & Clean Resigned Staff
                        synced_fp_count = 0
                        cleaned_embs_count = 0
                        try:
                            if not df_sheet_emp.empty and 'NRP' in df_sheet_emp.columns:
                                active_nrps = set(df_sheet_emp['NRP'].dropna().astype(str).str.strip().tolist())
                                cleaned_embs_count = sheets_handler.cleanup_resigned_face_embeddings(master_sheet_id, active_nrps)
                                if cleaned_embs_count > 0:
                                    # Refresh fp_mappings after cleanup so local DuckDB sync gets updated list
                                    fp_mappings = sheets_handler.get_fingerprint_mappings(master_sheet_id)

                            if fp_mappings is not None:
                                emp_db_handler.save_fingerprint_mappings(fp_mappings)
                                synced_fp_count = len(fp_mappings)
                        except Exception as fp_err:
                            logger.error(f"Error syncing employee mappings: {fp_err}")

                        # 6. Save SVP Approvals
                        synced_svp_count = 0
                        svp_cleared = False
                        try:
                            if not df_sheet_svp.empty:
                                synced_svp_count = emp_db_handler.save_svp_approval_records(df_sheet_svp)
                                svp_cleared = sheets_handler.clear_svp_approval_records(master_sheet_id)
                        except Exception as svp_err:
                            logger.error(f"Error syncing SVP approvals: {svp_err}")

                        # 7. Purge older records
                        purged_count = 0
                        retention_enabled = st.session_state.get("retention_enabled", True)
                        retention_months = st.session_state.get("retention_months", 6)
                        if retention_enabled:
                            purged_count = emp_db_handler.purge_attendance_records(retention_months)

                        # Log admin event
                        db_handler.log_admin_action(
                            "REFRESH_DATA", 
                            f"Synced employee master. Synced {synced_outlets_count} outlets. Imported {imported_count} scans. Cleared older sheet records (retained last 2 months): {cleared}. Cleaned {cleaned_embs_count} resigned face embeddings. Synced {synced_fp_count} employee mappings. Synced {synced_svp_count} SVP approvals. Purged: {purged_count} records."
                        )

                        st.success(f"Sync complete! Saved employee master list, synced {synced_outlets_count} outlets, imported {imported_count} logs, cleared sheets queue (retained last 2 months), cleaned {cleaned_embs_count} resigned employee face embeddings, synced {synced_fp_count} employee mappings, synced {synced_svp_count} supervisor approvals (cleared queue: {svp_cleared}), and purged {purged_count} expired records.")

                        st.cache_data.clear()
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Sync process failed: {e}")

        # Log Out button on the rightmost
        with col_logout:
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            if st.button("Log Out", type="secondary", use_container_width=False):
                st.session_state.admin_authenticated = False
                try:
                    st.query_params.pop("auth", None)
                except AttributeError:
                    try:
                        st.experimental_set_query_params()
                    except Exception:
                        pass
                db_handler.log_admin_action("LOGOUT", "Admin logged out.")
                st.rerun()

        # Shutdown button on the rightmost
        with col_shutdown:
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            if st.button("❌", use_container_width=False, key="shutdown_btn", help="Shutdown HR Admin Portal"):
                st.session_state.shutdown_triggered = True
                db_handler.log_admin_action("SHUTDOWN", "Admin triggered portal shutdown.")
                st.rerun()
                        

        # 1. Load employees and outlets first
        with st.spinner("Loading employee & outlet list..."):
            df_employees = load_employees_cached(emp_db_handler)
            df_outlets = load_outlets_cached(emp_db_handler)
            if df_outlets.empty:
                try:
                    credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                    credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                    s_handler = GoogleSheetsHandler(credentials_file, allow_offline=True)
                    df_outlets = s_handler.get_outlets_data(master_sheet_id)
                    if not df_outlets.empty:
                        emp_db_handler.save_outlets(df_outlets)
                except Exception:
                    pass

            
        # 2. Render Global Filter Panel
        with st.container(border=True):
            f_col1, f_col2, f_col_am, f_col3, f_col4 = st.columns(5)
            
            with f_col1:
                default_end = datetime.now().date()
                default_start = default_end.replace(day=1)
                selected_range = st.date_input(
                    "Date",
                    value=(default_start, default_end),
                    max_value=datetime.now().date(),
                    key="global_dashboard_date_range",
                    help="Applies to all charts and raw logs"
                )
            
            with f_col2:
                if not df_employees.empty and "Outlet" in df_employees.columns:
                    outlets = sorted([str(x).strip() for x in df_employees['Outlet'].dropna().unique() if str(x).strip() != ""])
                else:
                    outlets = []
                selected_outlet = st.multiselect("Outlet Location", options=outlets, default=[], placeholder="All Outlets", help="Select one or multiple outlets. Leave empty for All Outlets.")
                
            with f_col_am:
                if not df_employees.empty and "AM Baru" in df_employees.columns:
                    ams = sorted([str(x).strip() for x in df_employees['AM Baru'].dropna().unique() if str(x).strip() != ""])
                else:
                    ams = []
                selected_am = st.multiselect("Area Manager", options=ams, default=[], placeholder="All Area Managers", help="Select one or multiple Area Managers. Leave empty for All.")

            with f_col3:
                pos_col = 'Posisi Update' if ('Posisi Update' in df_employees.columns) else ('Posisi' if 'Posisi' in df_employees.columns else None)
                if not df_employees.empty and pos_col:
                    positions = sorted([str(x).strip() for x in df_employees[pos_col].dropna().unique() if str(x).strip() != ""])
                else:
                    positions = []
                selected_position = st.multiselect("Job Role / Position", options=positions, default=[], placeholder="All Positions", help="Select one or multiple Positions. Leave empty for All.")
                
            with f_col4:
                profile_options = [
                    "Over-Active Worker",
                    "Diligent Overachiever",
                    "Standard/Disciplined Worker",
                    "Compensatory Hard Worker",
                    "Habitual Late Comer",
                    "Early Clock-Out Specialist",
                    "Extended Breaker",
                    "Slipping Attendance / Habitual Late & Early",
                    "Forgetful Logger",
                    "Chronic Absentee",
                    "No Records"
                ]
                p_absent_t = int(st.session_state.get("p_absent_thresh", 0.35) * 100)
                p_missing_t = int(st.session_state.get("p_missing_thresh", 0.15) * 100)
                p_slipping_t1 = int(st.session_state.get("p_slipping_thresh1", 0.15) * 100)
                p_slipping_t2 = int(st.session_state.get("p_slipping_thresh2", 0.25) * 100)
                p_compensatory_t = int(st.session_state.get("p_compensatory_thresh", 0.20) * 100)
                p_late_t = int(st.session_state.get("p_late_thresh", 0.25) * 100)
                p_early_t = int(st.session_state.get("p_early_thresh", 0.25) * 100)
                p_overtime_t = int(st.session_state.get("p_overtime_thresh", 0.30) * 100)
                p_break_t = int(st.session_state.get("p_break_thresh", 0.25) * 100)
                p_break_m = int(st.session_state.get("p_break_mins", 60))

                help_text = (
                    "**Behavior Aggregation Algorithm Categories (Window-Based) - Sorted Good to Bad:**\n\n"
                    "1. **Over-Active Worker**: Active working days exceed outlet HK (Hari Kerja).\n"
                    f"2. **Diligent Overachiever**: Works overtime on >{p_overtime_t}% of active days with high punctuality.\n"
                    "3. **Standard/Disciplined Worker**: Adheres strictly to shift bounds and break time rules.\n"
                    f"4. **Compensatory Hard Worker**: Late clock-in but stays late/overtime on >{p_compensatory_t}% of active days.\n"
                    f"5. **Habitual Late Comer**: Arrives late frequently (>{p_late_t}%) but departs on time.\n"
                    f"6. **Early Clock-Out Specialist**: Frequently leaves work early (>{p_early_t}%) but arrives on time.\n"
                    f"7. **Extended Breaker**: Takes breaks exceeding {p_break_m} minutes on >{p_break_t}% of active days.\n"
                    f"8. **Slipping Attendance / Habitual Late & Early**: Both late clock-in and early clock-out on >{p_slipping_t1}% or combined >{p_slipping_t2}% of active days.\n"
                    f"9. **Forgetful Logger**: Missing clock-in or clock-out on >{p_missing_t}% of active days.\n"
                    f"10. **Chronic Absentee**: Absent on >{p_absent_t}% of outlet HK (Hari Kerja).\n"
                    "11. **No Records**: Active working days = 0 (employee has zero scan records in observed period)."
                )
                selected_profile_type = st.multiselect("Behavior Profile Category", options=profile_options, default=[], placeholder="All Profiles", help=help_text)

        # 3. Parse selected range to string format for fast DB queries
        start_date = default_start
        end_date = default_end
        
        if isinstance(selected_range, (tuple, list)):
            if len(selected_range) == 2:
                start_date, end_date = selected_range
            elif len(selected_range) == 1:
                start_date = selected_range[0]
                end_date = selected_range[0]
                
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        
        # 4. Load local database information (using cache helper functions)
        with st.spinner("Loading database records..."):
            df_attendance = load_attendance_cached(emp_db_handler, start_date_str, end_date_str)

        # Tab layouts
        tab_analytics, tab_profile_builder, tab_employees, tab_outlets, tab_am_task, tab_records, tab_fingerprint, tab_settings = st.tabs([
            "📊 Attendance Analytics",
            "🛠️ Profile Builder",
            "👥 Master Employee",
            "🏪 Outlets",
            "👔 AM Task",
            "📅 Attendance Log",
            "📱 Device Management",
            "⚙️ Admin & Retention Settings"
        ])



        # TAB 1: ATTENDANCE ANALYTICS
        with tab_analytics:
            # Compute attendance metrics with active filters
            with st.spinner("Calculating advanced behavior profiles and occupancy trends..."):
                metrics = compute_analytics(
                    df_attendance, 
                    df_employees, 
                    df_outlets=df_outlets,
                    selected_outlet=selected_outlet, 
                    selected_am=selected_am, 
                    selected_position=selected_position, 
                    start_date=start_date, 
                    end_date=end_date,
                    selected_profile_type=selected_profile_type
                )
            
            st.markdown(" ") # Spacing helper

            # Overview Metric row (8 KPI cards)
            col0, col1, col2, col3, col4, col5, col6, col7 = st.columns(8)
            with col0:
                st.markdown(f"""
                <div class="stat-card card-emerald">
                    <h4>Total Outlets</h4>
                    <p>{metrics.get("total_outlets", 0)}</p>
                </div>
                """, unsafe_allow_html=True)
            with col1:
                st.markdown(f"""
                <div class="stat-card card-blue">
                    <h4>Total Staff</h4>
                    <p>{metrics["total_active_staff"]}</p>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                st.markdown(f"""
                <div class="stat-card card-purple">
                    <h4>Daily Avg Present</h4>
                    <p>{metrics["avg_daily_headcount"]:.1f}</p>
                </div>
                """, unsafe_allow_html=True)
            with col3:
                st.markdown(f"""
                <div class="stat-card card-emerald">
                    <h4>Avg Working</h4>
                    <p>{metrics["avg_working_hours"]:.2f}h</p>
                </div>
                """, unsafe_allow_html=True)
            with col4:
                st.markdown(f"""
                <div class="stat-card card-amber">
                    <h4>Late In Rate</h4>
                    <p>{metrics["late_in_rate"]:.1f}%</p>
                </div>
                """, unsafe_allow_html=True)
            with col5:
                st.markdown(f"""
                <div class="stat-card card-blue">
                    <h4>Daily Avg Break</h4>
                    <p>{metrics["avg_break_duration"]:.1f}m</p>
                </div>
                """, unsafe_allow_html=True)
            with col6:
                st.markdown(f"""
                <div class="stat-card card-rose">
                    <h4>Early Out Rate</h4>
                    <p>{metrics["early_out_rate"]:.1f}%</p>
                </div>
                """, unsafe_allow_html=True)
            with col7:
                st.markdown(f"""
                <div class="stat-card card-purple">
                    <h4>Daily Avg Overtime</h4>
                    <p>{metrics["avg_daily_overtime"]:.2f}h</p>
                </div>
                """, unsafe_allow_html=True)


# Section 1: Daily Scan & Status Distribution Patterns
            st.markdown("---")
            st.write("**📊 Scan & Status Distribution Patterns**")
            
            # Row 2.1: Subplots pie and Hourly scans
            col_pie, col_hourly = st.columns([1, 1])
            with col_pie:
                st.write("**🍩 Clock-In & Clock-Out Status Breakdown**")
                
                has_checkin = not metrics["status_distribution"].empty
                has_checkout = "checkout_status_distribution" in metrics and not metrics["checkout_status_distribution"].empty
                
                if has_checkin or has_checkout:
                    from plotly.subplots import make_subplots
                    import plotly.graph_objects as go
                    
                    fig_pie = make_subplots(
                        rows=1, cols=2, 
                        specs=[[{"type": "domain"}, {"type": "domain"}]],
                        subplot_titles=["Clock-In Status", "Clock-Out Status"]
                    )
                    
                    # Colors: Map standard colors
                    color_map = {
                        "On Time": "#10B981",
                        "Early In": "#3B82F6",
                        "Late In": "#F59E0B",
                        "Early Out": "#F43F5E",
                        "Late Out": "#3B82F6",
                        "Over Time": "#8B5CF6"
                    }
                    
                    if has_checkin:
                        df_ci = metrics["status_distribution"]
                        ci_colors = [color_map.get(s, "#A78BFA") for s in df_ci['status']]
                        fig_pie.add_trace(
                            go.Pie(
                                labels=df_ci['status'].tolist(),
                                values=df_ci['Count'].tolist(),
                                hole=0.4,
                                name="Clock-in",
                                marker=dict(colors=ci_colors),
                                textposition='inside',
                                textinfo='label+percent'
                            ),
                            1, 1
                        )
                        
                    if has_checkout:
                        df_co = metrics["checkout_status_distribution"]
                        co_colors = [color_map.get(s, "#A78BFA") for s in df_co['status']]
                        fig_pie.add_trace(
                            go.Pie(
                                labels=df_co['status'].tolist(),
                                values=df_co['Count'].tolist(),
                                hole=0.4,
                                name="Clock-out",
                                marker=dict(colors=co_colors),
                                textposition='inside',
                                textinfo='label+percent'
                            ),
                            1, 2
                        )
                        
                    fig_pie.update_layout(
                        template='plotly_dark',
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=0, r=0, t=30, b=0),
                        height=320,
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5)
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("No status breakdown data available.")
                    
            with col_hourly:
                st.write("**🕒 Hourly Scan Distribution**")
                if not metrics["hourly_distribution"].empty:
                    fig_hour = px.bar(
                        metrics["hourly_distribution"],
                        x='Hour Interval',
                        y='Scans',
                        color='Scan Type',
                        barmode='group',
                        labels={'Hour Interval': 'Hour Block', 'Scans': 'Scan Count', 'Scan Type': 'Activity Type'},
                        template='plotly_dark',
                        color_discrete_map={
                            'Clock-in': '#60A5FA',    # Light Blue
                            'Clock-out': '#8B5CF6',   # Purple
                            'Start Break': '#F59E0B', # Amber
                            'End Break': '#10B981'    # Emerald
                        },
                        category_orders={
                            'Scan Type': ['Clock-in', 'Start Break', 'End Break', 'Clock-out']
                        }
                    )
                    fig_hour.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=20, r=20, t=20, b=20),
                        height=320,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title=dict(text='')),
                        xaxis=dict(categoryorder='trace')
                    )
                    st.plotly_chart(fig_hour, use_container_width=True)
                else:
                    st.info("No hourly distribution data available.")
                    
            # Row 2.2: Line trend and Heatmap side-by-side
            col_trend, col_heat = st.columns([1.1, 0.9])
            with col_trend:
                st.write("**📈 Daily Attendance Volume Trend**")
                if not metrics["daily_scans"].empty:
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots

                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    fig.add_trace(
                        go.Scatter(
                            x=metrics["daily_scans"]['date'].astype(str).tolist(), 
                            y=metrics["daily_scans"]['Employees Present'].tolist(), 
                            name="Employees Present",
                            mode="lines+markers",
                            line=dict(color="#60A5FA", width=3),
                            marker=dict(size=8)
                        ),
                        secondary_y=False,
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=metrics["daily_scans"]['date'].astype(str).tolist(), 
                            y=metrics["daily_scans"]['Total Scans'].tolist(), 
                            name="Total Scans",
                            mode="lines+markers",
                            line=dict(color="#A78BFA", width=2, dash="dash"),
                            marker=dict(size=6)
                        ),
                        secondary_y=True,
                    )
                    fig.update_layout(
                        template='plotly_dark',
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=20, r=20, t=20, b=20),
                        height=320,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    # Calculate max values with fallbacks and a 10% buffer to avoid clipping
                    max_employees = metrics["daily_scans"]['Employees Present'].max()
                    max_scans = metrics["daily_scans"]['Total Scans'].max()
                    if pd.isna(max_employees) or max_employees <= 0:
                        max_employees = 10
                    if pd.isna(max_scans) or max_scans <= 0:
                        max_scans = 10

                    fig.update_yaxes(
                        title_text="Employees Present (Headcount)", 
                        secondary_y=False, 
                        range=[0, max_employees * 1.1]
                    )
                    fig.update_yaxes(
                        title_text="Total Scans (Logs Volume)", 
                        secondary_y=True, 
                        range=[0, max_scans * 1.1]
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No logs match the selected filter criteria to display trend chart.")
                    
            with col_heat:
                st.write("**🌡️ Employee Hourly Occupancy Heatmap (Monday to Sunday)**")
                heatmap_df = metrics.get("heatmap_data")
                if heatmap_df is not None and not heatmap_df.empty:
                    import plotly.graph_objects as go
                    
                    x_labels = []
                    for h in range(24):
                        if h == 0:
                            x_labels.append("12 AM")
                        elif h < 12:
                            x_labels.append(f"{h} AM")
                        elif h == 12:
                            x_labels.append("12 PM")
                        else:
                            x_labels.append(f"{h-12} PM")
                    
                    text_annotations = []
                    for row in heatmap_df.values:
                        row_text = []
                        for val in row:
                            v = float(val)
                            if v == 0:
                                row_text.append("")
                            elif v.is_integer():
                                row_text.append(str(int(v)))
                            else:
                                row_text.append(f"{v:.1f}")
                        text_annotations.append(row_text)
                        
                    fig_heat = go.Figure(data=go.Heatmap(
                        z=heatmap_df.values.tolist(),
                        x=x_labels,
                        y=heatmap_df.index.tolist(),
                        colorscale='Blues',
                        text=text_annotations,
                        # texttemplate="%{text}",
                        textfont={"size": 10, "weight": "bold"},
                        hovertemplate='Day: %{y}<br>Hour: %{x}<br>Active Employees: %{z:.1f}<extra></extra>',
                        showscale=True
                    ))
                    fig_heat.update_layout(
                        template='plotly_dark',
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=40, r=20, t=10, b=40),
                        height=320,
                        xaxis=dict(
                            tickmode='array',
                            tickvals=x_labels,
                            ticktext=x_labels,
                            gridcolor='rgba(255,255,255,0.05)',
                            zeroline=False
                        ),
                        yaxis=dict(
                            type='category',
                            autorange="reversed",
                            gridcolor='rgba(255,255,255,0.05)',
                            zeroline=False
                        )
                    )
                    st.plotly_chart(fig_heat, use_container_width=True)
                else:
                    st.info("No data available to display occupancy heatmap.")


            # Section 2: Overall Behavioral Analysis
            st.markdown("---")
            st.write("**🎭 Overall Employee Behavior & Compliance**")
            with st.container(border=True):
                df_profiles = metrics.get("behavior_profiles", pd.DataFrame())
                df_consolidated = metrics.get("df_consolidated_unfiltered", pd.DataFrame())
                if not df_consolidated.empty:
                    
                    # Apply behavior filter if chosen
                    if isinstance(selected_profile_type, (list, tuple, set)) and selected_profile_type:
                        df_filtered_consolidated = df_consolidated[df_consolidated['Consolidated Profile'].isin(selected_profile_type)]
                        profile_label = ", ".join(selected_profile_type)
                    elif isinstance(selected_profile_type, str) and selected_profile_type != "All" and selected_profile_type != "":
                        df_filtered_consolidated = df_consolidated[df_consolidated['Consolidated Profile'] == selected_profile_type]
                        profile_label = selected_profile_type
                    else:
                        df_filtered_consolidated = df_consolidated
                        profile_label = "All Profiles"
                    
                    p_col1, p_col2 = st.columns([1.1, 0.9])
                    
                    # Define categories order & color mapping
                    CATEGORIES_ORDER = [
                        "Over-Active Worker",
                        "Diligent Overachiever",
                        "Standard/Disciplined Worker",
                        "Compensatory Hard Worker",
                        "Habitual Late Comer",
                        "Early Clock-Out Specialist",
                        "Extended Breaker",
                        "Slipping Attendance / Habitual Late & Early",
                        "Forgetful Logger",
                        "Chronic Absentee",
                        "No Records"
                    ]
                    CATEGORIES_COLORS = {
                        "Over-Active Worker": "#059669",                  # Deep Emerald
                        "Diligent Overachiever": "#10B981",              # Emerald Green
                        "Standard/Disciplined Worker": "#3B82F6",         # Blue
                        "Compensatory Hard Worker": "#06B6D4",            # Cyan
                        "Habitual Late Comer": "#FBBF24",                 # Yellow/Amber
                        "Early Clock-Out Specialist": "#F97316",           # Orange
                        "Extended Breaker": "#FCA5A5",                    # Light Red/Salmon
                        "Slipping Attendance / Habitual Late & Early": "#EF4444", # Red
                        "Forgetful Logger": "#8B5CF6",                    # Purple
                        "Chronic Absentee": "#B91C1C",                    # Dark Crimson
                        "No Records": "#64748B"                           # Slate Gray
                    }
                    
                    with p_col1:
                        st.write("**🎭 Employee Attendance Behavior Profiles (Consolidated)**")
                        st.write(f"**Staff matching '{profile_label}' ({len(df_filtered_consolidated)} employees):**")
                        
                        # Pie chart of consolidated behavior profiles sorted good to bad
                        profile_counts = df_consolidated['Consolidated Profile'].value_counts().reset_index()
                        profile_counts.columns = ['Profile Type', 'Count']
                        profile_counts['SortOrder'] = profile_counts['Profile Type'].map(lambda x: CATEGORIES_ORDER.index(x) if x in CATEGORIES_ORDER else 99)
                        profile_counts = profile_counts.sort_values('SortOrder')
                        
                        fig_profile_pie = px.pie(
                            profile_counts,
                            names='Profile Type',
                            values='Count',
                            hole=0.4,
                            template='plotly_dark',
                            color='Profile Type',
                            color_discrete_map=CATEGORIES_COLORS
                        )
                        fig_profile_pie.update_layout(
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            margin=dict(l=10, r=10, t=10, b=10),
                            height=320,
                            legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.02)
                        )
                        st.plotly_chart(fig_profile_pie, use_container_width=True)
                        
                    with p_col2:
                        st.dataframe(
                            df_filtered_consolidated,
                            use_container_width=False,
                            hide_index=True
                        )
                        
                    # Bubble chart: Behavior Profile vs Expected Hours vs Working Hours
                    st.write("**🫧 Employee Behavioral Distribution & Work Effort (Bubble Chart)**")
                    
                    df_bubble = df_consolidated.groupby('Consolidated Profile').agg(
                        Avg_Expected_Working_Hours=('Expected Working Hours', 'mean'),
                        Avg_Total_Working_Hours=('Total Working Hours', 'mean'),
                        Total_Employees=('NRP', 'count')
                    ).reset_index()
                    
                    fig_bubble = px.scatter(
                        df_bubble,
                        x='Avg_Expected_Working_Hours',
                        y='Avg_Total_Working_Hours',
                        size='Total_Employees',
                        color='Consolidated Profile',
                        hover_name='Consolidated Profile',
                        size_max=35,
                        template='plotly_dark',
                        color_discrete_map=CATEGORIES_COLORS,
                        labels={
                            'Avg_Expected_Working_Hours': 'Average Expected Total Working Hours',
                            'Avg_Total_Working_Hours': 'Average Total Working Hours (h)',
                            'Total_Employees': 'Total Employees',
                            'Consolidated Profile': 'Consolidated Profile'
                        }
                    )
                    # Range from 0 to max
                    max_x = df_bubble['Avg_Expected_Working_Hours'].max() if not df_bubble.empty else 100
                    max_y = df_bubble['Avg_Total_Working_Hours'].max() if not df_bubble.empty else 100
                    fig_bubble.update_xaxes(range=[0, max_x * 1.15 if max_x > 0 else 100])
                    fig_bubble.update_yaxes(range=[0, max_y * 1.15 if max_y > 0 else 100])
                    
                    # Add x = y threshold line
                    max_val = max(max_x, max_y)
                    fig_bubble.add_shape(
                        type="line",
                        x0=0,
                        y0=0,
                        x1=max_val * 1.2 if max_val > 0 else 100,
                        y1=max_val * 1.2 if max_val > 0 else 100,
                        line=dict(
                            color="rgba(0, 0, 0, 0.3)",
                            width=2,
                            dash="dash"
                        ),
                        layer="below"
                    )
                    
                    # Add text label annotation for the threshold line
                    fig_bubble.add_annotation(
                        x=max_val * 0.2 if max_val > 0 else 70,
                        y=max_val * 0.2 if max_val > 0 else 70,
                        text="Expected vs Actual Threshold",
                        showarrow=False,
                        yshift=10,
                        font=dict(color="rgba(0, 0, 0, 0.6)", size=10)
                    )
                    
                    fig_bubble.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=10, r=10, t=20, b=10),
                        height=350
                    )
                    st.plotly_chart(fig_bubble, use_container_width=True)
                        
                    # Drill-down UI inside this section
                    st.markdown("---")
                    st.write("**🔍 Employee Daily Attendance Drill-down**")
                    
                    # Dropdown of all employees found in consolidated profiles
                    all_employees = sorted(df_consolidated['Name'].tolist())
                    selected_drilldown_emp = st.selectbox(
                        "Select Employee to view daily attendance history and daily patterns:",
                        options=all_employees,
                        key="drilldown_emp_select"
                    )
                    
                    if selected_drilldown_emp:
                        # Filter daily profiles for this employee
                        df_emp_daily = df_profiles[df_profiles['Name'] == selected_drilldown_emp].sort_values("Date").copy()
                        
                        # Display clean dataframe showing Date, daily behavior pattern, and times
                        st.dataframe(
                            df_emp_daily[['Date', 'Pattern', 'Clock In', 'Clock Out', 'Break (mins)', 'Work Hours']],
                            use_container_width=False,
                            hide_index=True
                        )
                else:
                    st.info("No data available to display behavior profiles.")


            # Section 3: Compliance Alerts & Exceptions
            st.markdown("---")
            st.write("**⚠️ Compliance Alerts & Exceptions**")
            
            col_viol, col_audit = st.columns([1, 1])
            with col_viol:
                st.write("**🚨 Critical Attendance Violations (Top 5)**")
                tab_late, tab_early_out, tab_overtime = st.tabs(["Late In Staff", "Early Out Staff", "Over Time Staff"])
                
                with tab_late:
                    if not metrics["top_late_staff"].empty:
                        fig_bar = px.bar(
                            metrics["top_late_staff"],
                            x='Late Count',
                            y='employee_name',
                            orientation='h',
                            labels={'Late Count': 'Late Scans', 'employee_name': 'Employee'},
                            template='plotly_dark',
                            color='Late Count',
                            color_continuous_scale='Oranges'
                        )
                        fig_bar.update_layout(
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            showlegend=False,
                            coloraxis_showscale=False,
                            margin=dict(l=20, r=20, t=20, b=20),
                            height=270
                        )
                        st.plotly_chart(fig_bar, use_container_width=True)
                    else:
                        st.success("🎉 No late clock-ins recorded for this selection!")
                        
                with tab_early_out:
                    if "top_early_out_staff" in metrics and not metrics["top_early_out_staff"].empty:
                        fig_eo_bar = px.bar(
                            metrics["top_early_out_staff"],
                            x='Early Out Count',
                            y='employee_name',
                            orientation='h',
                            labels={'Early Out Count': 'Early Out Scans', 'employee_name': 'Employee'},
                            template='plotly_dark',
                            color='Early Out Count',
                            color_continuous_scale='Reds'
                        )
                        fig_eo_bar.update_layout(
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            showlegend=False,
                            coloraxis_showscale=False,
                            margin=dict(l=20, r=20, t=20, b=20),
                            height=270
                        )
                        st.plotly_chart(fig_eo_bar, use_container_width=True)
                    else:
                        st.success("🎉 No early clock-outs recorded for this selection!")
                        
                with tab_overtime:
                    if "top_overtime_staff" in metrics and not metrics["top_overtime_staff"].empty:
                        fig_ot_bar = px.bar(
                            metrics["top_overtime_staff"],
                            x='Total Overtime Hours',
                            y='employee_name',
                            orientation='h',
                            labels={'Total Overtime Hours': 'Overtime (Hours)', 'employee_name': 'Employee'},
                            template='plotly_dark',
                            color='Total Overtime Hours',
                            color_continuous_scale='Purples'
                        )
                        fig_ot_bar.update_layout(
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            showlegend=False,
                            coloraxis_showscale=False,
                            margin=dict(l=20, r=20, t=20, b=20),
                            height=270
                        )
                        st.plotly_chart(fig_ot_bar, use_container_width=True)
                    else:
                        st.success("🎉 No overtime recorded for this selection!")
                        
            with col_audit:
                st.write("**🔍 Behavioral Exceptions & Audit Logs**")
                abnormal = metrics["abnormal_shifts"]
                if abnormal:
                    df_abnormal = pd.DataFrame(abnormal)
                    st.warning(f"⚠️ **Attention Required:** Found {len(abnormal)} attendance anomalies (missing scans, short shifts < 6h).")
                    st.dataframe(
                        df_abnormal[["Date", "Name", "Details"]], 
                        use_container_width=False, 
                        hide_index=True
                    )
                else:
                    st.success("✅ **Perfect Logs:** No shift duration exceptions or missing log pairs discovered!")
                    
            # Diagnostic warnings for security
            if metrics["unregistered_scans"] > 0:
                st.error(f"🚨 **Security Warning:** Detected {metrics['unregistered_scans']} unregistered scan attempt(s). Please review raw attendance logs to investigate.")

            # Section 4: Performance Comparison
            st.markdown("---")
            st.write("**🏢 Outlet Attendance Performance**")
            if not metrics["outlet_performance"].empty:
                fig_outlet = px.bar(
                    metrics["outlet_performance"],
                    x='Outlet',
                    y=['Late Rate (%)', 'Early Out Rate (%)', 'Overtime Rate (%)'],
                    barmode='group',
                    hover_data=['Total Scans', 'Avg Hours'],
                    labels={'value': 'Rate (%)', 'variable': 'Metric'},
                    template='plotly_dark',
                    color_discrete_sequence=['#F59E0B', '#F43F5E', '#8B5CF6'],
                    title="Late In Rate (%), Early Out Rate (%) & Overtime Rate (%) by Outlet"
                )
                fig_outlet.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    margin=dict(l=20, r=20, t=90, b=20),
                    height=350,
                    xaxis=dict(categoryorder='trace'),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="center",
                        x=0.5,
                        title=dict(text='')
                    )
                )
                st.plotly_chart(fig_outlet, use_container_width=True)
            else:
                st.info("No outlet performance metrics available.")
            # Section 5: Scan Distance & GPS Accuracy Analysis
            st.markdown("---")
            st.write("**📍 Scan Distance & GPS Accuracy Analysis**")
            
            d_col1, d_col2, d_col3 = st.columns(3)
            with d_col1:
                st.markdown(f"""
                <div class="stat-card card-blue">
                    <h4>Avg Scan Distance</h4>
                    <p>{metrics.get("avg_distance_meters", 0.0):.1f} m</p>
                    <span style="font-size:0.75rem; color:var(--text-muted);">Max: {metrics.get("max_distance_meters", 0.0):.0f} m</span>
                </div>
                """, unsafe_allow_html=True)
            with d_col2:
                st.markdown(f"""
                <div class="stat-card card-emerald">
                    <h4>Avg GPS Accuracy</h4>
                    <p>{metrics.get("avg_gps_accuracy", 0.0):.1f} m</p>
                    <span style="font-size:0.75rem; color:var(--text-muted);">Terminal Precision</span>
                </div>
                """, unsafe_allow_html=True)
            with d_col3:
                st.markdown(f"""
                <div class="stat-card card-purple">
                    <h4>High Accuracy Rate</h4>
                    <p>{metrics.get("high_accuracy_rate", 0.0):.1f}%</p>
                    <span style="font-size:0.75rem; color:var(--text-muted);">Precision ≤ 50 meters</span>
                </div>
                """, unsafe_allow_html=True)

            st.write("**📊 Distance & GPS Accuracy Distribution by Outlet (Box Plot Analysis)**")
            df_raw = metrics.get("raw_merged_df", pd.DataFrame())
            if df_raw.empty and not df_attendance.empty:
                df_raw = df_attendance.copy()

            if not df_raw.empty:
                if '_dist_num' not in df_raw.columns and 'distance_meters' in df_raw.columns:
                    df_raw['_dist_num'] = pd.to_numeric(df_raw['distance_meters'].astype(str).str.extract(r'(\d+)')[0], errors='coerce')
                if '_acc_num' not in df_raw.columns and 'gps_accuracy' in df_raw.columns:
                    df_raw['_acc_num'] = pd.to_numeric(df_raw['gps_accuracy'].astype(str).str.extract(r'(\d+)')[0], errors='coerce')

            if not df_raw.empty and 'Outlet' in df_raw.columns:
                all_outlets_list = sorted([str(o) for o in df_raw['Outlet'].dropna().unique()])
                
                col_f1, col_f2 = st.columns([3, 1])
                with col_f1:
                    selected_outlets = st.multiselect(
                        "🔍 Filter Outlet Box Plot (Default: Semua Outlet)",
                        options=all_outlets_list,
                        default=all_outlets_list,
                        key="box_plot_outlet_filter"
                    )
                with col_f2:
                    chart_orientation = st.radio(
                        "Orientasi Bagan",
                        options=["Vertikal (X-axis)", "Horisontal (Y-axis)"],
                        index=0,
                        key="box_chart_orientation"
                    )

                if selected_outlets:
                    df_raw_chart = df_raw[df_raw['Outlet'].astype(str).isin(selected_outlets)].copy()
                else:
                    df_raw_chart = df_raw.copy()

                num_outlets = len(df_raw_chart['Outlet'].dropna().unique())
                
                box_tab1, box_tab2 = st.tabs(["📍 Scan Distance Box Plot", "📡 GPS Accuracy Box Plot"])
                
                with box_tab1:
                    df_valid_dist = df_raw_chart.dropna(subset=['_dist_num']).copy()
                    if not df_valid_dist.empty:
                        hover_cols = [c for c in ['employee_name', 'NRP', 'timestamp', 'type', 'notes'] if c in df_valid_dist.columns]
                        
                        if chart_orientation == "Horisontal (Y-axis)":
                            calc_height = max(420, num_outlets * 22)
                            fig_box_dist = px.box(
                                df_valid_dist,
                                y='Outlet',
                                x='_dist_num',
                                points='outliers',
                                color='Outlet',
                                hover_data=hover_cols,
                                labels={'_dist_num': 'Distance (Meters)', 'Outlet': 'Outlet'},
                                title=f"Scan Distance (Meters) Box Plot ({num_outlets} Outlets)",
                                template='plotly_dark'
                            )
                            fig_box_dist.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                margin=dict(l=20, r=20, t=40, b=20),
                                height=calc_height,
                                showlegend=False
                            )
                        else:
                            fig_box_dist = px.box(
                                df_valid_dist,
                                x='Outlet',
                                y='_dist_num',
                                points='outliers',
                                color='Outlet',
                                hover_data=hover_cols,
                                labels={'_dist_num': 'Distance (Meters)', 'Outlet': 'Outlet'},
                                title=f"Scan Distance (Meters) Box Plot ({num_outlets} Outlets)",
                                template='plotly_dark'
                            )
                            fig_box_dist.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                margin=dict(l=20, r=20, t=40, b=80),
                                height=480,
                                showlegend=False
                            )
                            fig_box_dist.update_xaxes(tickangle=-45, tickfont=dict(size=10))

                        st.plotly_chart(fig_box_dist, use_container_width=True)
                    else:
                        st.info("No distance data available for Box Plot.")

                with box_tab2:
                    df_valid_acc = df_raw_chart.dropna(subset=['_acc_num']).copy()
                    if not df_valid_acc.empty:
                        hover_cols = [c for c in ['employee_name', 'NRP', 'timestamp', 'type', 'notes'] if c in df_valid_acc.columns]
                        
                        if chart_orientation == "Horisontal (Y-axis)":
                            calc_height = max(420, num_outlets * 22)
                            fig_box_acc = px.box(
                                df_valid_acc,
                                y='Outlet',
                                x='_acc_num',
                                points='outliers',
                                color='Outlet',
                                hover_data=hover_cols,
                                labels={'_acc_num': 'GPS Accuracy (Meters)', 'Outlet': 'Outlet'},
                                title=f"GPS Accuracy Precision (Meters) Box Plot ({num_outlets} Outlets)",
                                template='plotly_dark'
                            )
                            fig_box_acc.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                margin=dict(l=20, r=20, t=40, b=20),
                                height=calc_height,
                                showlegend=False
                            )
                        else:
                            fig_box_acc = px.box(
                                df_valid_acc,
                                x='Outlet',
                                y='_acc_num',
                                points='outliers',
                                color='Outlet',
                                hover_data=hover_cols,
                                labels={'_acc_num': 'GPS Accuracy (Meters)', 'Outlet': 'Outlet'},
                                title=f"GPS Accuracy Precision (Meters) Box Plot ({num_outlets} Outlets)",
                                template='plotly_dark'
                            )
                            fig_box_acc.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                margin=dict(l=20, r=20, t=40, b=80),
                                height=480,
                                showlegend=False
                            )
                            fig_box_acc.update_xaxes(tickangle=-45, tickfont=dict(size=10))

                        st.plotly_chart(fig_box_acc, use_container_width=True)
                    else:
                        st.info("No GPS accuracy data available for Box Plot.")

                # Anomaly Records Pointing Out Table
                st.write("**🚨 Detected Distance & GPS Accuracy Anomaly Records**")
                
                df_anom = df_raw.copy()
                reasons = []
                
                # Calculate IQR outlier threshold per outlet
                outlet_iqr = {}
                for outlet_name, group in df_anom.groupby('Outlet'):
                    q3_d = group['_dist_num'].quantile(0.75) if '_dist_num' in group.columns else 0
                    q1_d = group['_dist_num'].quantile(0.25) if '_dist_num' in group.columns else 0
                    iqr_d = q3_d - q1_d
                    
                    q3_a = group['_acc_num'].quantile(0.75) if '_acc_num' in group.columns else 0
                    q1_a = group['_acc_num'].quantile(0.25) if '_acc_num' in group.columns else 0
                    iqr_a = q3_a - q1_a
                    
                    outlet_iqr[outlet_name] = {
                        'dist_thresh': q3_d + 1.5 * iqr_d if iqr_d > 0 else 50,
                        'acc_thresh': q3_a + 1.5 * iqr_a if iqr_a > 0 else 50
                    }
                
                for idx, row in df_anom.iterrows():
                    r_list = []
                    d_val = row.get('_dist_num', pd.NA)
                    a_val = row.get('_acc_num', pd.NA)
                    out_name = row.get('Outlet', '')
                    
                    if pd.notna(d_val) and d_val > 50:
                        r_list.append(f"🚨 Distance > 50m ({d_val:.0f}m)")
                    if pd.notna(a_val) and a_val > 100:
                        r_list.append(f"⚠️ Poor GPS Precision ({a_val:.0f}m)")
                    elif pd.notna(a_val) and a_val == 0:
                        r_list.append("⚠️ GPS Inactive / 0m")
                        
                    if out_name in outlet_iqr:
                        th_d = outlet_iqr[out_name]['dist_thresh']
                        th_a = outlet_iqr[out_name]['acc_thresh']
                        if pd.notna(d_val) and d_val > th_d and d_val > 30 and f"🚨 Distance > 50m ({d_val:.0f}m)" not in r_list:
                            r_list.append(f"📊 Distance Outlier (> {th_d:.0f}m)")
                        if pd.notna(a_val) and a_val > th_a and a_val > 30 and f"⚠️ Poor GPS Precision ({a_val:.0f}m)" not in r_list:
                            r_list.append(f"📊 Accuracy Outlier (> {th_a:.0f}m)")
                            
                    reasons.append(" | ".join(r_list) if r_list else None)
                    
                df_anom['Anomaly Category'] = reasons
                df_flagged = df_anom[df_anom['Anomaly Category'].notna()].copy()
                
                if not df_flagged.empty:
                    st.warning(f"⚠️ Identified **{len(df_flagged)}** anomaly record(s) based on IQR statistical outliers and radius/accuracy thresholds:")
                    display_cols = ['timestamp', 'NRP', 'employee_name', 'Outlet', 'type', 'distance_meters', 'gps_accuracy', 'Anomaly Category', 'notes']
                    existing_display = [c for c in display_cols if c in df_flagged.columns]
                    st.dataframe(df_flagged[existing_display].sort_values('timestamp', ascending=False), use_container_width=False, hide_index=True)
                else:
                    st.success("✅ No distance or GPS accuracy anomalies detected in the current filter selection.")
            else:
                st.info("No distance data available for outlets.")

        # TAB: PROFILE BUILDER
        with tab_profile_builder:
            st.markdown("### 🛠️ Behavior Profile Rules & Threshold Builder")
            st.write("Customize the rule thresholds for the Window-Based Behavior Aggregation Algorithm. Adjust the percentages below to recalibrate behavior profile assignments dynamically.")
            
            # Group variables by categories: Punctuality, Absence, Work Effort
            col_b1, col_b2 = st.columns(2)
            
            with col_b1:
                st.write("**🔴 Absence & Logging Rules**")
                p_absent = st.slider(
                    "Chronic Absentee Threshold (% of HK / Hari Kerja)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_absent_thresh * 100),
                    help="Categorized if absent days exceed this percentage of outlet HK (Hari Kerja)."
                )
                p_missing = st.slider(
                    "Forgetful Logger Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_missing_thresh * 100),
                    help="Categorized if missing clock-in or clock-out scans exceed this percentage of active days."
                )
                
                st.write("**🟡 Punctuality Rules**")
                p_late = st.slider(
                    "Habitual Late Comer Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_late_thresh * 100),
                    help="Categorized if late clock-ins (without staying late/overtime) exceed this percentage."
                )
                p_early = st.slider(
                    "Early Clock-Out Specialist Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_early_thresh * 100),
                    help="Categorized if early clock-outs exceed this percentage of active days."
                )
                
            with col_b2:
                st.write("**🟢 Work Effort & Break Rules**")
                p_overtime = st.slider(
                    "Diligent Overachiever Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_overtime_thresh * 100),
                    help="Categorized if overtime days (working >1h over shift end) exceed this percentage."
                )
                p_compensatory = st.slider(
                    "Compensatory Hard Worker Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_compensatory_thresh * 100),
                    help="Categorized if late clock-ins with corresponding overtime exceed this percentage."
                )
                p_break = st.slider(
                    "Extended Breaker Threshold (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_break_thresh * 100),
                    help="Categorized if break durations exceeding the limit occur on more than this percentage of days."
                )
                p_break_m = st.slider(
                    "Extended Break Limit (Minutes)",
                    min_value=30, max_value=180, step=5,
                    value=int(st.session_state.p_break_mins),
                    help="Define the threshold duration above which a daily break is marked as extended."
                )
                
            st.write("**⚠️ Combined / Slipping Rules**")
            col_b3, col_b4 = st.columns(2)
            with col_b3:
                p_slip1 = st.slider(
                    "Slipping Attendance Threshold 1 (% of active days)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_slipping_thresh1 * 100),
                    help="Slipping attendance profile threshold (overall late and early patterns)."
                )
            with col_b4:
                p_slip2 = st.slider(
                    "Slipping Attendance Threshold 2 (Combined Rate %)",
                    min_value=5, max_value=100, step=5,
                    value=int(st.session_state.p_slipping_thresh2 * 100),
                    help="Combined late-in and early-out occurrence percentage."
                )

            st.write("**⏰ Attendance Scan Status Timing Thresholds (Minutes)**")
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                early_in_m_val = st.slider(
                    "Early Clock-In Threshold (Minutes before shift start)",
                    min_value=0, max_value=60, step=5,
                    value=int(st.session_state.get("early_in_thresh_min", 10)),
                    help="Clock-ins occurring more than this many minutes before shift start will be marked as 'Early In'."
                )
                clock_in_g_val = st.slider(
                    "Clock-In Grace Period (Minutes prior to shift start)",
                    min_value=0, max_value=60, step=5,
                    value=int(st.session_state.get("clock_in_grace_min", 10)),
                    help="Clock-ins within this many minutes prior to shift start up to shift start will be marked as 'On Time'."
                )
            with col_t2:
                clock_out_g_val = st.slider(
                    "Clock-Out Grace Period (Minutes after shift end)",
                    min_value=0, max_value=60, step=5,
                    value=int(st.session_state.get("clock_out_grace_min", 10)),
                    help="Clock-outs within this many minutes after shift end will be marked as 'On Time'."
                )
                late_out_m_val = st.slider(
                    "Late Clock-Out Threshold (Minutes after shift end before Overtime)",
                    min_value=30, max_value=360, step=10,
                    value=int(st.session_state.get("late_out_thresh_min", 120)),
                    help="Clock-outs between clock-out grace period and this limit are marked as 'Late Out'. Clock-outs beyond this limit are marked as 'Over Time'."
                )

            col_action1, col_action2, _ = st.columns([1.5, 1.5, 5])
            with col_action1:
                if st.button("💾 Apply Configuration", type="primary", use_container_width=False, key="save_profile_rules"):
                    st.session_state.p_absent_thresh = p_absent / 100.0
                    st.session_state.p_missing_thresh = p_missing / 100.0
                    st.session_state.p_slipping_thresh1 = p_slip1 / 100.0
                    st.session_state.p_slipping_thresh2 = p_slip2 / 100.0
                    st.session_state.p_compensatory_thresh = p_compensatory / 100.0
                    st.session_state.p_late_thresh = p_late / 100.0
                    st.session_state.p_early_thresh = p_early / 100.0
                    st.session_state.p_overtime_thresh = p_overtime / 100.0
                    st.session_state.p_break_thresh = p_break / 100.0
                    st.session_state.p_break_mins = p_break_m
                    st.session_state.early_in_thresh_min = early_in_m_val
                    st.session_state.clock_in_grace_min = clock_in_g_val
                    st.session_state.clock_out_grace_min = clock_out_g_val
                    st.session_state.late_out_thresh_min = late_out_m_val

                    save_profile_thresholds({
                        "p_absent_thresh": p_absent / 100.0,
                        "p_missing_thresh": p_missing / 100.0,
                        "p_slipping_thresh1": p_slip1 / 100.0,
                        "p_slipping_thresh2": p_slip2 / 100.0,
                        "p_compensatory_thresh": p_compensatory / 100.0,
                        "p_late_thresh": p_late / 100.0,
                        "p_early_thresh": p_early / 100.0,
                        "p_overtime_thresh": p_overtime / 100.0,
                        "p_break_thresh": p_break / 100.0,
                        "p_break_mins": p_break_m,
                        "early_in_thresh_min": early_in_m_val,
                        "clock_in_grace_min": clock_in_g_val,
                        "clock_out_grace_min": clock_out_g_val,
                        "late_out_thresh_min": late_out_m_val
                    })

                    db_handler.log_admin_action("UPDATE_PROFILE_RULES", "Admin updated employee behavior profile thresholds.")
                    st.success("Configuration updated successfully!")
                    st.rerun()
            with col_action2:
                if st.button("🔄 Reset to Defaults", use_container_width=False, key="reset_profile_rules"):
                    st.session_state.p_absent_thresh = 0.35
                    st.session_state.p_missing_thresh = 0.15
                    st.session_state.p_slipping_thresh1 = 0.15
                    st.session_state.p_slipping_thresh2 = 0.25
                    st.session_state.p_compensatory_thresh = 0.20
                    st.session_state.p_late_thresh = 0.25
                    st.session_state.p_early_thresh = 0.25
                    st.session_state.p_overtime_thresh = 0.30
                    st.session_state.p_break_thresh = 0.25
                    st.session_state.p_break_mins = 60
                    st.session_state.early_in_thresh_min = 10
                    st.session_state.clock_in_grace_min = 10
                    st.session_state.clock_out_grace_min = 10
                    st.session_state.late_out_thresh_min = 120

                    save_profile_thresholds({
                        "p_absent_thresh": 0.35,
                        "p_missing_thresh": 0.15,
                        "p_slipping_thresh1": 0.15,
                        "p_slipping_thresh2": 0.25,
                        "p_compensatory_thresh": 0.20,
                        "p_late_thresh": 0.25,
                        "p_early_thresh": 0.25,
                        "p_overtime_thresh": 0.30,
                        "p_break_thresh": 0.25,
                        "p_break_mins": 60,
                        "early_in_thresh_min": 10,
                        "clock_in_grace_min": 10,
                        "clock_out_grace_min": 10,
                        "late_out_thresh_min": 120
                    })

                    db_handler.log_admin_action("RESET_PROFILE_RULES", "Admin reset employee behavior profile thresholds to defaults.")
                    st.info("Reset to default thresholds.")
                    st.rerun()


            # Live preview simulation
            st.markdown("---")
            st.write("#### 🔍 Real-Time Impact Simulation")
            st.write("Below is the simulation of behavior profile categories based on current parameters:")
            
            # Recalculate consolidated profile distribution using current parameters on the loaded data
            df_preview_profiles = metrics.get("behavior_profiles_unfiltered", pd.DataFrame())
            if not df_preview_profiles.empty:
                simulated_records = []
                for (nrp, name), group in df_preview_profiles.groupby(['NRP', 'Name']):
                    # Temporarily override parameters for preview if user hasn't pressed save yet
                    temp_state = {
                        "p_absent_thresh": p_absent / 100.0,
                        "p_missing_thresh": p_missing / 100.0,
                        "p_slipping_thresh1": p_slip1 / 100.0,
                        "p_slipping_thresh2": p_slip2 / 100.0,
                        "p_compensatory_thresh": p_compensatory / 100.0,
                        "p_late_thresh": p_late / 100.0,
                        "p_early_thresh": p_early / 100.0,
                        "p_overtime_thresh": p_overtime / 100.0,
                        "p_break_thresh": p_break / 100.0,
                        "p_break_mins": p_break_m
                    }
                    
                    # Back up original session state
                    orig_state = {k: st.session_state.get(k) for k in temp_state.keys()}
                    # Set temporary values for simulation
                    for k, v in temp_state.items():
                        st.session_state[k] = v
                        
                    # Calculate profile
                    profile = aggregate_employee_profile(group)
                    
                    # Revert original values
                    for k, v in orig_state.items():
                        if v is not None:
                            st.session_state[k] = v
                    
                    simulated_records.append({"Profile": profile})
                
                df_sim = pd.DataFrame(simulated_records)
                sim_counts = df_sim["Profile"].value_counts().reset_index()
                sim_counts.columns = ["Behavior Profile Category", "Active Employee Headcount"]
                
                st.dataframe(sim_counts, use_container_width=False, hide_index=True)
            else:
                st.info("No attendance log data loaded to preview simulation. Adjust global filters above to load logs.")

        # TAB 2: EMPLOYEE MASTER
        with tab_employees:
            if not df_employees.empty:
                df_emp_disp = df_employees.copy()
                if "Posisi" in df_emp_disp.columns:
                    df_emp_disp = df_emp_disp.drop(columns=["Posisi"])
                st.dataframe(df_emp_disp, use_container_width=False)
            else:
                st.warning("Employee table is currently empty. Run **Refresh Data** below to sync from Google Sheets.")

        # TAB 3: OUTLETS MASTER
        with tab_outlets:
            st.markdown("### 🏪 Master Outlets & Schedule Management")
            
            sub_tab_geo, sub_tab_sched = st.tabs(["⚙️ Outlet Settings", "⏰ Outlet Shift Options"])
            
            with sub_tab_geo:
                if not df_outlets.empty:
                    try:
                        from modules.google_sheets import deduplicate_outlets_df
                        df_outlets_clean = deduplicate_outlets_df(df_outlets)
                    except Exception:
                        df_outlets_clean = df_outlets.copy()

                    # Ensure HK column exists with default 22
                    if "HK" not in df_outlets_clean.columns:
                        df_outlets_clean["HK"] = 22
                    else:
                        df_outlets_clean["HK"] = pd.to_numeric(df_outlets_clean["HK"], errors='coerce').fillna(22).astype(int)

                    col_o1, col_o2 = st.columns([1, 4])
                    with col_o1:
                        st.metric("Total Registered Outlets", len(df_outlets_clean))

                    search_outlet = st.text_input("🔍 Search Outlet Name", "", key="search_outlet_geo").strip()
                    df_outlets_display = df_outlets_clean.copy()
                    if search_outlet:
                        cols_to_search = [c for c in df_outlets_display.columns if df_outlets_display[c].dtype == object or str(df_outlets_display[c].dtype).startswith('string')]
                        mask = pd.Series(False, index=df_outlets_display.index)
                        for c in cols_to_search:
                            mask = mask | df_outlets_display[c].astype(str).str.contains(search_outlet, case=False, na=False)
                        df_outlets_display = df_outlets_display[mask]

                    # Ensure numeric types for Latitude, Longitude, and HK
                    for col in ["Latitude", "Longitude"]:
                        if col in df_outlets_display.columns:
                            df_outlets_display[col] = pd.to_numeric(df_outlets_display[col], errors='coerce')
                    if "HK" in df_outlets_display.columns:
                        df_outlets_display["HK"] = pd.to_numeric(df_outlets_display["HK"], errors='coerce').fillna(22).astype(int)

                    # Render Editable Data Table
                    edited_df = st.data_editor(
                        df_outlets_display,
                        column_config={
                            "Outlet": st.column_config.TextColumn("Outlet", disabled=True),
                            "Latitude": st.column_config.NumberColumn("Latitude", format="%.6f", help="Contoh: -6.174647"),
                            "Longitude": st.column_config.NumberColumn("Longitude", format="%.6f", help="Contoh: 106.696405"),
                            "Radius": st.column_config.NumberColumn("Radius (Meter)", min_value=1, max_value=5000, step=5, help="Radius geofencing dalam meter"),
                            "Secret": st.column_config.TextColumn("Secret Key Base32", disabled=True),
                            "HK": st.column_config.NumberColumn("HK (Hari Kerja)", min_value=1, max_value=31, step=1, default=22, help="Default Hari Kerja per bulan (default: 22)")
                        },
                        disabled=["Outlet", "Secret"],
                        use_container_width=False,
                        num_rows="fixed",
                        key="outlets_data_editor"
                    )

                    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
                    if st.button("💾 Save Outlet Settings to Google Sheets", type="primary", use_container_width=False, key="btn_save_outlets"):
                        with st.spinner("Saving outlet settings to Google Sheets & local database..."):
                            try:
                                # Merge edits back into df_outlets_clean if search filter was active
                                if search_outlet:
                                    for idx, row in edited_df.iterrows():
                                        o_name = row.get("Outlet")
                                        match_idx = df_outlets_clean[df_outlets_clean["Outlet"] == o_name].index
                                        if not match_idx.empty:
                                            for c in ["Latitude", "Longitude", "Radius", "HK"]:
                                                if c in row:
                                                    df_outlets_clean.loc[match_idx, c] = row[c]
                                    save_df = df_outlets_clean
                                else:
                                    save_df = edited_df

                                # 1. Save to local DuckDB database
                                emp_db_handler.save_outlets(save_df)

                                # 2. Upload to Google Sheets
                                master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                                credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                                credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                                s_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)

                                if s_handler.online and master_sheet_id:
                                    save_success = s_handler.save_outlets_data(master_sheet_id, save_df)
                                    if save_success:
                                        db_handler.log_admin_action("SAVE_OUTLET_SETTINGS", f"Saved outlet settings for {len(save_df)} outlets to Google Sheets")
                                        st.success("✅ Outlet settings successfully saved to Google Sheets and local database!")
                                        st.cache_data.clear()
                                        time.sleep(1.2)
                                        st.rerun()
                                    else:
                                        st.error("❌ Failed to save outlets to Google Sheets.")
                                else:
                                    st.error("❌ Google Sheets connection is offline. Cannot save changes.")
                            except Exception as save_err:
                                logger.error(f"Error saving outlet settings: {save_err}")
                                st.error(f"❌ Error saving outlet settings: {save_err}")
                else:
                    st.warning("Outlets table is currently empty. Click **🔄 Refresh Data** in the top-right to sync outlets from Google Sheets (`MP Database` & `Outlets` tab).")

            with sub_tab_sched:
                st.markdown("#### ⏰ Outlet Shift Options & Template Management")
                st.caption("Buat template shift (misal: Mall Standard, Ruko Standard) dan hubungkan template ke outlet. Setiap perubahan template/assignment otomatis memperbarui tab 'Outlet Schedule' di Google Sheets.")
                
                # Fetch templates and assignments
                df_templates = emp_db_handler.get_shift_templates()
                if df_templates.empty or not set(['Template Name', 'Shift', 'Working Hour']).issubset(set(df_templates.columns)):
                    df_templates = pd.DataFrame([
                        {"Template Name": "Mall Standard", "Shift": "Shift 1", "Working Hour": "09:00 - 18:00"},
                        {"Template Name": "Mall Standard", "Shift": "Middle", "Working Hour": "10:00 - 20:00"},
                        {"Template Name": "Mall Standard", "Shift": "Middle closing", "Working Hour": "10:00 - 22:00"},
                        {"Template Name": "Mall Standard", "Shift": "Shift 2", "Working Hour": "12:00 - 22:00"},
                        {"Template Name": "Ruko Standard", "Shift": "Shift 1", "Working Hour": "08:00 - 17:00"},
                        {"Template Name": "Ruko Standard", "Shift": "Middle", "Working Hour": "10:00 - 20:00"},
                        {"Template Name": "Ruko Standard", "Shift": "Shift 2", "Working Hour": "12:00 - 22:00"}
                    ])

                df_assignments = emp_db_handler.get_outlet_template_assignments()
                if df_assignments.empty or not set(['Outlet', 'Template Name']).issubset(set(df_assignments.columns)):
                    df_assignments = pd.DataFrame(columns=['Outlet', 'Template Name'])

                df_schedules = emp_db_handler.get_outlet_schedules()
                if df_schedules.empty or not set(['Outlet', 'Working Hour', 'Shift']).issubset(set(df_schedules.columns)):
                    df_schedules = pd.DataFrame(columns=['Outlet', 'Working Hour', 'Shift'])

                outlets_list = []
                if not df_outlets.empty and 'Outlet' in df_outlets.columns:
                    outlets_list = sorted([str(x).strip() for x in df_outlets['Outlet'].dropna().unique() if str(x).strip()])
                elif not df_schedules.empty and 'Outlet' in df_schedules.columns:
                    outlets_list = sorted([str(x).strip() for x in df_schedules['Outlet'].dropna().unique() if str(x).strip()])

                # Helper to compute and push resolved outlet schedules
                def auto_sync_resolved_outlet_schedules(df_tpl, df_asgn, df_sched, o_list):
                    resolved_rows = []
                    asgn_dict = {}
                    if not df_asgn.empty and 'Outlet' in df_asgn.columns and 'Template Name' in df_asgn.columns:
                        asgn_dict = dict(zip(df_asgn['Outlet'].astype(str).str.strip(), df_asgn['Template Name'].astype(str).str.strip()))

                    for o in o_list:
                        tpl_name = asgn_dict.get(o)
                        if tpl_name and tpl_name not in ["None / Custom", "", "nan"]:
                            tpl_shifts = df_tpl[df_tpl['Template Name'] == tpl_name]
                            if not tpl_shifts.empty:
                                for _, r in tpl_shifts.iterrows():
                                    resolved_rows.append({
                                        "Outlet": o,
                                        "Working Hour": str(r.get("Working Hour", "")).strip(),
                                        "Shift": str(r.get("Shift", "")).strip()
                                    })
                                continue

                        cust_shifts = df_sched[df_sched['Outlet'] == o] if not df_sched.empty and 'Outlet' in df_sched.columns else pd.DataFrame()
                        if not cust_shifts.empty:
                            for _, r in cust_shifts.iterrows():
                                resolved_rows.append({
                                    "Outlet": o,
                                    "Working Hour": str(r.get("Working Hour", "")).strip(),
                                    "Shift": str(r.get("Shift", "")).strip()
                                })

                    df_res = pd.DataFrame(resolved_rows) if resolved_rows else pd.DataFrame(columns=["Outlet", "Working Hour", "Shift"])
                    
                    emp_db_handler.save_outlet_schedules(df_res)
                    master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                    credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                    credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                    s_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                    if s_handler.online and master_sheet_id:
                        s_handler.save_outlet_schedules_data(master_sheet_id, df_res)
                    return len(df_res)

                # Sub-sections using radio selection
                sched_mode = st.radio("Select Shift Management View:", options=["📋 Shift Templates Library", "🔗 Assign Templates to Outlets"], horizontal=True, key="radio_sched_mode")

                if sched_mode == "📋 Shift Templates Library":
                    st.markdown("##### 📋 Shift Templates Library")
                    
                    template_names = sorted([str(x).strip() for x in df_templates['Template Name'].dropna().unique() if str(x).strip()])
                    selected_tpl = st.selectbox("🔍 Filter by Template Name", options=["All Templates"] + template_names, key="select_filter_tpl")
                    
                    if selected_tpl != "All Templates":
                        df_tpl_display = df_templates[df_templates["Template Name"] == selected_tpl].copy()
                    else:
                        df_tpl_display = df_templates.copy()

                    edited_tpl_df = st.data_editor(
                        df_tpl_display,
                        column_config={
                            "Template Name": st.column_config.TextColumn("Nama Template", required=True, help="Contoh: Mall Standard, Ruko Standard"),
                            "Shift": st.column_config.TextColumn("Nama Shift", help="Contoh: Shift 1, Shift 2, Middle, Middle closing"),
                            "Working Hour": st.column_config.TextColumn("Working Hour (Jam Kerja)", help="Contoh: 09:00 - 18:00")
                        },
                        num_rows="dynamic",
                        use_container_width=False,
                        key="editor_shift_templates"
                    )

                    col_tpl_s1, col_tpl_s2 = st.columns([1, 1])
                    with col_tpl_s1:
                        if st.button("💾 Save Shift Templates", type="primary", use_container_width=False, key="btn_save_shift_tpl"):
                            with st.spinner("Saving Shift Templates & updating Outlet Schedules..."):
                                try:
                                    if selected_tpl != "All Templates":
                                        df_other_tpl = df_templates[df_templates["Template Name"] != selected_tpl]
                                        final_tpl_df = pd.concat([df_other_tpl, edited_tpl_df], ignore_index=True)
                                    else:
                                        final_tpl_df = edited_tpl_df.copy()

                                    final_tpl_df = final_tpl_df.dropna(subset=['Template Name'])
                                    final_tpl_df['Template Name'] = final_tpl_df['Template Name'].astype(str).str.strip()
                                    final_tpl_df = final_tpl_df[final_tpl_df['Template Name'] != '']

                                    emp_db_handler.save_shift_templates(final_tpl_df)

                                    master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                                    credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                                    credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                                    s_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                                    if s_handler.online and master_sheet_id:
                                        s_handler.save_shift_templates_data(master_sheet_id, final_tpl_df)

                                    # Auto sync resolved outlet schedules to Google Sheets
                                    pushed_cnt = auto_sync_resolved_outlet_schedules(final_tpl_df, df_assignments, df_schedules, outlets_list)

                                    st.success(f"✅ Shift Templates saved & {pushed_cnt} outlet schedule records synced to Google Sheets!")
                                    time.sleep(1.2)
                                    st.rerun()
                                except Exception as err:
                                    st.error(f"❌ Error saving Shift Templates: {err}")

                    with col_tpl_s2:
                        with st.expander("➕ Create New Template Quick Form", expanded=False):
                            with st.form("form_create_tpl", clear_on_submit=True):
                                new_tpl_name = st.text_input("New Template Name", placeholder="e.g. Mall Standard")
                                new_tpl_shift = st.text_input("First Shift Name", placeholder="e.g. Shift 1")
                                new_tpl_wh = st.text_input("First Working Hour", placeholder="e.g. 08:00 - 17:00")
                                btn_create_tpl = st.form_submit_button("Create Template")
                                if btn_create_tpl:
                                    if new_tpl_name and new_tpl_shift and new_tpl_wh:
                                        new_tpl_row = pd.DataFrame([{
                                            "Template Name": new_tpl_name.strip(),
                                            "Shift": new_tpl_shift.strip(),
                                            "Working Hour": new_tpl_wh.strip()
                                        }])
                                        updated_tpl = pd.concat([df_templates, new_tpl_row], ignore_index=True)
                                        emp_db_handler.save_shift_templates(updated_tpl)
                                        
                                        master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                                        credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                                        credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                                        s_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                                        if s_handler.online and master_sheet_id:
                                            s_handler.save_shift_templates_data(master_sheet_id, updated_tpl)
                                            
                                        auto_sync_resolved_outlet_schedules(updated_tpl, df_assignments, df_schedules, outlets_list)
                                        st.success(f"Template '{new_tpl_name}' created and schedules updated!")
                                        time.sleep(1.0)
                                        st.rerun()

                elif sched_mode == "🔗 Assign Templates to Outlets":
                    st.markdown("##### 🔗 Assign Shift Templates to Outlets")
                    st.caption("Pilih outlet dan hubungkan dengan Shift Template. Menimpan perubahan di sini akan langsung memperbarui tab 'Outlet Schedule' di Google Sheets.")
                    
                    template_options = sorted([str(x).strip() for x in df_templates['Template Name'].dropna().unique() if str(x).strip()])
                    
                    # Ensure all registered outlets are present in assignments table
                    existing_asgn_outlets = set(df_assignments['Outlet'].astype(str).str.strip()) if not df_assignments.empty and 'Outlet' in df_assignments.columns else set()
                    missing_outlets = [o for o in outlets_list if o not in existing_asgn_outlets]
                    if missing_outlets:
                        new_asgn_rows = pd.DataFrame([{"Outlet": o, "Template Name": template_options[0] if template_options else "Custom"} for o in missing_outlets])
                        df_assignments = pd.concat([df_assignments, new_asgn_rows], ignore_index=True)

                    # Bulk assignment form
                    with st.expander("⚡ Bulk Assign Template to Multiple Outlets", expanded=False):
                        with st.form("form_bulk_assign"):
                            bulk_target_outlets = st.multiselect("Select Outlets to Assign", options=outlets_list)
                            bulk_selected_tpl = st.selectbox("Assign Template", options=template_options)
                            btn_bulk = st.form_submit_button("Apply Template to Selected Outlets")
                            if btn_bulk:
                                if bulk_target_outlets and bulk_selected_tpl:
                                    for o in bulk_target_outlets:
                                        match_idx = df_assignments[df_assignments['Outlet'] == o].index
                                        if not match_idx.empty:
                                            df_assignments.loc[match_idx, 'Template Name'] = bulk_selected_tpl
                                        else:
                                            df_assignments = pd.concat([df_assignments, pd.DataFrame([{"Outlet": o, "Template Name": bulk_selected_tpl}])], ignore_index=True)
                                    emp_db_handler.save_outlet_template_assignments(df_assignments)
                                    auto_sync_resolved_outlet_schedules(df_templates, df_assignments, df_schedules, outlets_list)
                                    st.success(f"Assigned '{bulk_selected_tpl}' to {len(bulk_target_outlets)} outlets and synced to Google Sheets!")
                                    time.sleep(1.0)
                                    st.rerun()

                    # Editable Assignments Table
                    edited_asgn_df = st.data_editor(
                        df_assignments,
                        column_config={
                            "Outlet": st.column_config.SelectboxColumn("Outlet", options=outlets_list, required=True),
                            "Template Name": st.column_config.SelectboxColumn("Assigned Template", options=template_options + ["None / Custom"], required=True)
                        },
                        num_rows="dynamic",
                        use_container_width=False,
                        key="editor_outlet_assignments"
                    )

                    if st.button("💾 Save Template Assignments", type="primary", use_container_width=False, key="btn_save_asgn"):
                        with st.spinner("Saving Template Assignments & syncing Outlet Schedules to Google Sheets..."):
                            try:
                                emp_db_handler.save_outlet_template_assignments(edited_asgn_df)
                                master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                                credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                                credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
                                s_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                                if s_handler.online and master_sheet_id:
                                    s_handler.save_outlet_template_assignments_data(master_sheet_id, edited_asgn_df)

                                # Always compute and push resolved outlet schedules to 'Outlet Schedule' tab in Google Sheets
                                pushed_cnt = auto_sync_resolved_outlet_schedules(df_templates, edited_asgn_df, df_schedules, outlets_list)

                                st.success(f"✅ Template Assignments saved & {pushed_cnt} outlet schedule records pushed to Google Sheets tab 'Outlet Schedule'!")
                                time.sleep(1.2)
                                st.rerun()
                            except Exception as err:
                                st.error(f"❌ Error saving template assignments: {err}")


        # TAB: AM TASK (AREA MANAGER APPROVAL TASKS)
        with tab_am_task:
            st.markdown("### 👔 Area Manager (AM) Approval Tasks & Status")
            st.caption("Pantau seluruh status perizinan dan tugas absensi karyawan yang memerlukan persetujuan Area Manager (Pending, Approved, Rejected).")

            df_merged = metrics.get("raw_merged_df", pd.DataFrame())
            if df_merged.empty and not df_attendance.empty:
                df_merged = df_attendance.copy()

            if not df_merged.empty and "notes" in df_merged.columns:
                has_appr_note = df_merged['notes'].astype(str).str.contains('Approval Required|APPROVED|REJECTED', case=False, na=False)
                has_appr_cat = df_merged['Case Category'].isin(['Lupa Absen', 'Izin Terlambat', 'Pulang Awal', 'Tugas Luar', 'Supervisor Approval', 'AM Approval']) if 'Case Category' in df_merged.columns else False
                df_am_tasks = df_merged[has_appr_note | has_appr_cat].copy()

                if not df_am_tasks.empty:
                    if 'AM Baru' not in df_am_tasks.columns and not df_employees.empty and 'AM Baru' in df_employees.columns:
                        emp_am_map = dict(zip(df_employees['NRP'].astype(str).str.strip(), df_employees['AM Baru'].astype(str).str.strip()))
                        df_am_tasks['AM Baru'] = df_am_tasks['NRP'].astype(str).str.strip().map(emp_am_map).fillna('-')

                    def resolve_task_category(row):
                        cc = str(row.get('Case Category', '')).strip()
                        if cc and cc not in ['', 'nan', 'None', 'NaN']:
                            return cc
                        notes = str(row.get('notes', ''))
                        if 'Exceeded Monthly HK' in notes or 'Exceeded HK' in notes:
                            return 'Melebihi HK Normal'
                        elif 'Belanja' in notes or 'Belanja Kebutuhan Outlet' in notes:
                            return 'Belanja Kebutuhan Outlet'
                        elif 'Tugas Luar' in notes:
                            return 'Tugas Luar Event'
                        elif 'Izin Terlambat' in notes:
                            return 'Izin Terlambat'
                        elif 'Pulang Awal' in notes:
                            return 'Pulang Awal'
                        elif 'Lupa Absen' in notes:
                            return 'Lupa Absen'
                        elif 'PERBANTUKAN' in notes or 'PENUGASAN SILANG' in notes or 'Outlet Perbantuan' in notes or 'Penugasan Silang Outlet' in notes:
                            return 'Penugasan Silang Outlet'
                        return 'Perizinan Absen'

                    df_am_tasks['Task Category'] = df_am_tasks.apply(resolve_task_category, axis=1)

                    def resolve_am_status(row):
                        notes = str(row.get('notes', '')).upper()
                        if 'APPROVED' in notes:
                            return 'APPROVED'
                        elif 'REJECTED' in notes:
                            return 'REJECTED'
                        elif 'APPROVAL REQUIRED' in notes or 'WAITING' in str(row.get('Review Status', '')).upper():
                            return 'PENDING'
                        existing = str(row.get('Review Status', '')).strip()
                        if existing and existing not in ['', 'nan', 'None', 'NaN']:
                            return existing.upper()
                        return 'PENDING'

                    df_am_tasks['Approval Status'] = df_am_tasks.apply(resolve_am_status, axis=1)

                    col_flt_am, col_flt_st, col_flt_cat = st.columns(3)
                    with col_flt_am:
                        am_options = sorted([str(x).strip() for x in df_am_tasks['AM Baru'].dropna().unique() if str(x).strip() not in ['', 'nan', 'None']])
                        sel_am_filter = st.selectbox("👔 Filter by Area Manager", options=["All Area Managers"] + am_options, key="am_task_filter_am")

                    with col_flt_st:
                        sel_st_filter = st.selectbox("📊 Filter by Status", options=["All Statuses", "PENDING", "APPROVED", "REJECTED"], key="am_task_filter_st")

                    with col_flt_cat:
                        cat_options = sorted([str(x).strip() for x in df_am_tasks['Task Category'].dropna().unique() if str(x).strip() not in ['', 'nan', 'None']])
                        sel_cat_filter = st.selectbox("📁 Filter by Category", options=["All Categories"] + cat_options, key="am_task_filter_cat")

                    df_filtered_tasks = df_am_tasks.copy()
                    if sel_am_filter != "All Area Managers":
                        df_filtered_tasks = df_filtered_tasks[df_filtered_tasks['AM Baru'] == sel_am_filter]
                    if sel_st_filter != "All Statuses":
                        df_filtered_tasks = df_filtered_tasks[df_filtered_tasks['Approval Status'] == sel_st_filter]
                    if sel_cat_filter != "All Categories":
                        df_filtered_tasks = df_filtered_tasks[df_filtered_tasks['Task Category'] == sel_cat_filter]

                    cnt_pending = len(df_filtered_tasks[df_filtered_tasks['Approval Status'] == 'PENDING'])
                    cnt_approved = len(df_filtered_tasks[df_filtered_tasks['Approval Status'] == 'APPROVED'])
                    cnt_rejected = len(df_filtered_tasks[df_filtered_tasks['Approval Status'] == 'REJECTED'])
                    cnt_total = len(df_filtered_tasks)

                    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
                    with kpi1:
                        st.markdown(f"""
                        <div class="stat-card card-amber">
                            <h4>⏳ Pending AM Approval</h4>
                            <p>{cnt_pending}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    with kpi2:
                        st.markdown(f"""
                        <div class="stat-card card-emerald">
                            <h4>🟢 Approved Tasks</h4>
                            <p>{cnt_approved}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    with kpi3:
                        st.markdown(f"""
                        <div class="stat-card card-rose">
                            <h4>🔴 Rejected Tasks</h4>
                            <p>{cnt_rejected}</p>
                        </div>
                        """, unsafe_allow_html=True)
                    with kpi4:
                        st.markdown(f"""
                        <div class="stat-card card-blue">
                            <h4>📋 Total AM Tasks</h4>
                            <p>{cnt_total}</p>
                        </div>
                        """, unsafe_allow_html=True)

                    st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

                    ch_col1, ch_col2 = st.columns(2)
                    with ch_col1:
                        if not df_filtered_tasks.empty:
                            am_status_grp = df_filtered_tasks.groupby(['AM Baru', 'Approval Status']).size().reset_index(name='Count')
                            fig_am_st = px.bar(
                                am_status_grp,
                                x='AM Baru',
                                y='Count',
                                color='Approval Status',
                                color_discrete_map={"APPROVED": "#10B981", "REJECTED": "#EF4444", "PENDING": "#F59E0B"},
                                title="AM Approval Tasks by Area Manager",
                                barmode='group',
                                template='plotly_dark'
                            )
                            fig_am_st.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=320)
                            st.plotly_chart(fig_am_st, use_container_width=True)

                    with ch_col2:
                        if not df_filtered_tasks.empty:
                            cat_status_grp = df_filtered_tasks.groupby(['Task Category', 'Approval Status']).size().reset_index(name='Count')
                            fig_cat_st = px.bar(
                                cat_status_grp,
                                x='Task Category',
                                y='Count',
                                color='Approval Status',
                                color_discrete_map={"APPROVED": "#10B981", "REJECTED": "#EF4444", "PENDING": "#F59E0B"},
                                title="AM Tasks by Category",
                                barmode='group',
                                template='plotly_dark'
                            )
                            fig_cat_st.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=320)
                            st.plotly_chart(fig_cat_st, use_container_width=True)

                    st.write("**📋 Detail List of AM Approval Tasks**")
                    for col in ['employee_name', 'Nama Staff']:
                        if col in df_filtered_tasks.columns and 'Nama Staff' not in df_filtered_tasks.columns:
                            df_filtered_tasks['Nama Staff'] = df_filtered_tasks[col]

                    cols_exist = [c for c in ['date', 'time', 'NRP', 'Nama Staff', 'Outlet', 'AM Baru', 'Task Category', 'Approval Status', 'notes'] if c in df_filtered_tasks.columns]
                    df_am_table = df_filtered_tasks[cols_exist].copy()
                    df_am_table.rename(columns={
                        'date': 'Tanggal',
                        'time': 'Jam',
                        'notes': 'Catatan / Alasan'
                    }, inplace=True)

                    st.dataframe(df_am_table, use_container_width=True, hide_index=True)
                else:
                    st.info("ℹ️ No AM approval tasks found in the selected date range.")
            else:
                st.info("ℹ️ Attendance records database is empty or not loaded yet.")


        # TAB 5: ATTENDANCE RECORDS (EXPORT & VIEW)
        with tab_records:
            df_filtered = df_attendance.copy()
            
            if not df_filtered.empty:
                # Render export block
                with st.container(border=True):
                    col_info, col_export = st.columns([3, 1])
                    with col_info:
                        st.markdown(f"<p style='margin:12px 0 0 0; font-size:1.0rem; font-weight:500;'>Export attendance logs for the active filter range ({start_date_str} to {end_date_str})</p>", unsafe_allow_html=True)
                    with col_export:
                        start_str = start_date.strftime("%Y%m%d")
                        end_str = end_date.strftime("%Y%m%d")
                        export_filename = f"attendance_records_{start_str}_to_{end_str}.csv"
                        csv_data = df_filtered.to_csv(index=False).encode('utf-8')
                        
                        st.download_button(
                            label=f"CSV Export ({len(df_filtered)})",
                            data=csv_data,
                            file_name=export_filename,
                            mime="text/csv",
                            use_container_width=False
                        )
                
                # Table view for matching records
                st.write(f"**Records Table ({len(df_filtered)} logs found)**")
                filter_nrp = st.text_input("Search NRP in filtered range", "")
                df_view = df_filtered.copy()
                if filter_nrp.strip():
                    df_view = df_view[df_view["NRP"].astype(str).str.contains(filter_nrp.strip())]
                
                try:
                    from modules.google_sheets import populate_distance_and_accuracy
                    df_view = populate_distance_and_accuracy(df_view)
                except Exception as err:
                    logger.error(f"Error populating distance/accuracy: {err}")

                # Guarantee outlet column is present and populated
                if 'outlet' not in df_view.columns or df_view['outlet'].astype(str).str.strip().eq('').all():
                    if 'Outlet' in df_view.columns:
                        df_view['outlet'] = df_view['Outlet']

                if not df_employees.empty and 'NRP' in df_view.columns:
                    emp_outlet_map = dict(zip(df_employees['NRP'].astype(str).str.strip(), df_employees['Outlet']))
                    df_view['outlet'] = df_view.apply(
                        lambda r: str(r.get('outlet', '')).strip() if str(r.get('outlet', '')).strip() and str(r.get('outlet', '')).strip().lower() not in ['nan', 'none', '']
                        else emp_outlet_map.get(str(r.get('NRP', '')).strip(), ''),
                        axis=1
                    )

                try:
                    from modules.database import parse_case_category, parse_svp_details_from_notes
                    if 'notes' in df_view.columns:
                        df_view['Case Category'] = df_view['notes'].apply(parse_case_category)
                        svp_parsed = df_view.apply(lambda r: parse_svp_details_from_notes(r, df_employees), axis=1)
                        df_view['SVP NRP'] = svp_parsed['SVP NRP']
                        df_view['SVP Name'] = svp_parsed['SVP Name']
                        df_view['Review Status'] = svp_parsed['Review Status']
                        df_view['Review Date'] = svp_parsed['Review Date']
                except Exception as err:
                    logger.error(f"Error parsing case category & svp details: {err}")

                # Calculate dynamic scan status using profile thresholds
                early_in_m = st.session_state.get("early_in_thresh_min", 10)
                clock_in_g = st.session_state.get("clock_in_grace_min", 10)
                clock_out_g = st.session_state.get("clock_out_grace_min", 10)
                late_out_m = st.session_state.get("late_out_thresh_min", 120)

                df_view['status'] = df_view.apply(
                    lambda r: calculate_scan_status(
                        r,
                        early_in_thresh=early_in_m,
                        clock_in_grace=clock_in_g,
                        clock_out_grace=clock_out_g,
                        late_out_thresh=late_out_m
                    ),
                    axis=1
                )

                # Define column order: put 'status' right after 'type'
                cols_order = [
                    'NRP', 'employee_name', 'timestamp', 'type', 'status', 'outlet', 
                    'distance_meters', 'gps_accuracy', 'work_start', 'work_end', 
                    'notes', 'Case Category', 'SVP NRP', 'SVP Name', 'Review Status', 'Review Date'
                ]
                excluded_cols = ['id', 'fingerprint_id']
                existing_cols = [c for c in cols_order if c in df_view.columns]
                other_cols = [c for c in df_view.columns if c not in existing_cols and c not in excluded_cols]
                df_view = df_view[existing_cols + other_cols]

                
                st.dataframe(df_view, use_container_width=False)
            else:
                st.info(f"No attendance logs found in the selected range (**{start_date_str}** to **{end_date_str}**).")

        # TAB 5: DEVICE MANAGEMENT & FACE MAPPING
        with tab_fingerprint:
            st.markdown("### 📱 Device Management Dashboard")
            
            master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
            credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
            credentials_file = credentials_val if os.path.isabs(credentials_val) else os.path.join(PROJECT_ROOT, credentials_val)
            sheets_handler_req = GoogleSheetsHandler(credentials_file, allow_offline=True)

            # 2. Check for Pending Unbind Device Requests from Employees
            try:
                df_unbind_reqs = sheets_handler_req.get_unbind_device_requests(master_sheet_id)
                if not df_unbind_reqs.empty:
                    st.warning(f"📱 **{len(df_unbind_reqs)} Permintaan Unbind Device Pending** dari Karyawan!")
                    with st.expander("📋 Kelola Permintaan Unbind Device HP Baru", expanded=True):
                        for idx, u_row in df_unbind_reqs.iterrows():
                            u_nrp = str(u_row.get("NRP", "")).strip()
                            u_reason = str(u_row.get("Reason", "Ganti HP Baru")).strip()
                            u_date = str(u_row.get("Requested_At", "N/A")).strip()
                            
                            u_name = "Unknown Staff"
                            if not df_employees.empty:
                                match_emp = df_employees[df_employees['NRP'] == u_nrp]
                                if not match_emp.empty:
                                    u_name = match_emp.iloc[0].get('Nama Staff', 'Unknown')

                            col_u1, col_u2, col_u3 = st.columns([3, 1, 1])
                            with col_u1:
                                st.write(f"👤 **{u_nrp}** - {u_name} | *Alasan:* {u_reason} ({u_date})")
                            with col_u2:
                                if st.button("✅ Approve", key=f"app_un_{u_nrp}_{idx}", type="primary"):
                                    sheets_handler_req.process_unbind_device_request(master_sheet_id, u_nrp, "APPROVED")
                                    emp_db_handler.delete_fingerprint_mapping_by_nrp(u_nrp)
                                    st.session_state.pop('cached_face_embeddings', None)
                                    db_handler.log_admin_action("APPROVE_UNBIND_DEVICE", f"Approved device unbind & deleted Face Embedding for NRP {u_nrp}")
                                    st.success(f"Device ID dan data Face Embedding untuk {u_nrp} ({u_name}) berhasil dihapus / di-unbind!")
                                    time.sleep(1.0)
                                    st.rerun()
                            with col_u3:
                                if st.button("❌ Reject", key=f"rej_un_{u_nrp}_{idx}"):
                                    sheets_handler_req.process_unbind_device_request(master_sheet_id, u_nrp, "REJECTED")
                                    db_handler.log_admin_action("REJECT_UNBIND_DEVICE", f"Rejected device unbind for NRP {u_nrp}")
                                    st.info(f"Permintaan unbind NRP {u_nrp} ditolak.")
                                    time.sleep(1.0)
                                    st.rerun()
            except Exception as unbind_err:
                logger.error(f"Error checking unbind requests: {unbind_err}")


            # Load mappings from local DuckDB database cache
            mappings = emp_db_handler.get_fingerprint_mappings()
            mapped_nrps = set(str(v).strip() for v in mappings.values())
            
            # Calculate registration statistics
            total_employees = 0
            registered_count = 0
            unregistered_count = 0
            
            if not df_employees.empty:
                df_employees['NRP'] = df_employees['NRP'].astype(str).str.strip()
                total_employees = df_employees['NRP'].nunique()
                registered_count = df_employees[df_employees['NRP'].isin(mapped_nrps)]['NRP'].nunique()
                unregistered_count = total_employees - registered_count

            # Render metric cards
            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                st.metric("Total Employees", total_employees)
            with col_m2:
                st.metric("Face Registered", registered_count)
            with col_m3:
                st.metric("Not Yet Registered", unregistered_count)
                
            # Expandable list of unregistered employees
            if not df_employees.empty:
                df_unregistered = df_employees[~df_employees['NRP'].isin(mapped_nrps)]
                with st.expander(f"📋 View Not Yet Registered Staff ({unregistered_count})", expanded=False):
                    if not df_unregistered.empty:
                        disp_cols = [c for c in ["NRP", "Nama Staff", "Posisi Update", "Posisi", "Outlet"] if c in df_unregistered.columns]
                        if "Posisi Update" in disp_cols and "Posisi" in disp_cols:
                            disp_cols.remove("Posisi")
                        st.dataframe(df_unregistered[disp_cols], use_container_width=False)
                    else:
                        st.success("🎉 All employees have registered Face Embeddings / IDs!")

            st.markdown("---")
            st.markdown("#### **Registered Face Embeddings & Mappings**")
            
            # Format and display mapping table
            if mappings:
                # Ambil pemetaan Device ID dari Google Sheets (tab 'Face_Embedding')
                device_id_map = {}
                try:
                    device_id_map = sheets_handler_req.get_device_id_mappings(master_sheet_id)
                except Exception as dev_err:
                    logger.warning(f"Failed to load device_id_map from Google Sheets: {dev_err}")

                mappings_data = []
                for fp_id, nrp in mappings.items():
                    clean_nrp = str(nrp).strip()
                    emp_name = "Unknown Staff"
                    emp_outlet = "N/A"
                    emp_position = "N/A"
                    
                    if not df_employees.empty:
                        df_employees['NRP'] = df_employees['NRP'].astype(str).str.strip()
                        match = df_employees[df_employees['NRP'] == clean_nrp]
                        if not match.empty:
                            emp_name = match.iloc[0].get('Nama Staff', 'Unknown')
                            emp_outlet = match.iloc[0].get('Outlet', 'N/A')
                            emp_position = match.iloc[0].get('Posisi Update', match.iloc[0].get('Posisi', 'N/A'))
                            
                    dev_id_val = str(device_id_map.get(clean_nrp, "")).strip()
                    fp_str = str(fp_id).strip()
                    
                    # Abaikan jika nilai adalah array face embedding mentah yang diawali '[' atau '{'
                    if dev_id_val.startswith("[") or dev_id_val.startswith("{"):
                        dev_id_val = ""

                    if not dev_id_val and ("att_dev_" in fp_str or "dev_" in fp_str) and not fp_str.startswith("["):
                        dev_id_val = fp_str
                    
                    # Fallback simulasi Device ID resmi jika kosong atau bukan format device ID
                    if not dev_id_val or dev_id_val == "-" or dev_id_val.startswith("[") or dev_id_val.startswith("{"):
                        import hashlib
                        nrp_hash = hashlib.md5(clean_nrp.encode('utf-8')).hexdigest()[:12]
                        dev_id_val = f"att_dev_{nrp_hash}"

                    mappings_data.append({
                        "NRP": clean_nrp,
                        "Employee Name": emp_name,
                        "Outlet": emp_outlet,
                        "Position": emp_position,
                        "Device ID": dev_id_val
                    })
                
                df_mappings = pd.DataFrame(mappings_data)
                
                # Render tabel tanpa Display ID, menampilkan Device ID setelah Position
                st.dataframe(
                    df_mappings[["NRP", "Employee Name", "Outlet", "Position", "Device ID"]],
                    use_container_width=False
                )
                
                # Unbind Device control
                st.markdown("### 🗑️ Unbind Device")
                
                to_delete = st.multiselect(
                    "Select Employee(s) to unbind device:",
                    options=df_mappings["NRP"].unique(),
                    format_func=lambda x: f"{x} - {df_mappings[df_mappings['NRP'] == x]['Employee Name'].values[0]}"
                )
                
                if st.button("🗑️ Unbind Selected Device(s)", type="primary", use_container_width=False):
                    if to_delete:
                        # 1. Update the mapping dict to exclude selected NRPs
                        new_mappings = {k: v for k, v in mappings.items() if v not in to_delete}
                        
                        # 2. Upload updated mappings to Google Sheets
                        sync_success = True
                        master_sheet_id = os.getenv("QR_ATTENDANCE_SHEET_ID")
                        credentials_val = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
                        if not os.path.isabs(credentials_val):
                            credentials_file = os.path.join(PROJECT_ROOT, credentials_val)
                        else:
                            credentials_file = credentials_val
                            
                        with st.spinner("Updating Google Sheets..."):
                            try:
                                sheets_handler = GoogleSheetsHandler(credentials_file, allow_offline=False)
                                if sheets_handler.online and master_sheet_id:
                                    sheets_handler.save_fingerprint_mappings(master_sheet_id, new_mappings)
                                else:
                                    st.error("Google Sheets is offline. Cannot modify spreadsheet. Operation aborted.")
                                    sync_success = False
                            except Exception as e:
                                logger.error(f"Error updating Google Sheets during unbind: {e}")
                                st.error(f"Error updating Google Sheets: {e}")
                                sync_success = False
                                
                        if sync_success:
                            # 3. Save to local DuckDB database
                            emp_db_handler.save_fingerprint_mappings(new_mappings)
                            
                            # 4. Log admin event
                            db_handler.log_admin_action(
                                "UNBIND_DEVICE",
                                f"Unbound device & face embedding for NRPs: {', '.join(to_delete)}"
                            )
                            st.success(f"Successfully unbound device for: {', '.join(to_delete)}")
                            time.sleep(1.0)
                            st.rerun()
                    else:
                        st.warning("Please select at least one employee to unbind device.")
            else:
                st.info("No face embedding mappings are registered. Click 'Refresh Data' in the top-right to sync from Google Sheets (`Face_Embedding` tab).")

            # ================================================================= #
            # FRAUD DETECTION — Device Bind/Unbind History                       #
            # ================================================================= #
            st.markdown("---")
            st.markdown("### 🔍 Device Fraud Detection — Bind/Unbind History")

            try:
                col_sync1, col_sync2 = st.columns([3, 1])
                with col_sync2:
                    do_sync_history = st.button("🔄 Sync History", key="btn_sync_bind_history",
                                                help="Ambil data terbaru Device_Bind_History dari Google Sheets")

                # Sync dari Sheets ke local DB jika diminta
                if do_sync_history:
                    with st.spinner("Mengambil Device_Bind_History & Face_Embedding dari Google Sheets..."):
                        try:
                            df_hist_remote = sheets_handler_req.get_device_bind_history(master_sheet_id)
                            if not df_hist_remote.empty:
                                saved = db_handler.save_device_bind_history(df_hist_remote)
                                st.success(f"✅ {saved} event baru berhasil disinkronkan ke database lokal.")
                            else:
                                st.info("Belum ada data history di Google Sheets (tab Device_Bind_History).")
                        except Exception as sync_err:
                            st.error(f"Gagal sync history: {sync_err}")

                        try:
                            raw_embs = sheets_handler_req.get_raw_face_embeddings(master_sheet_id)
                            if raw_embs:
                                st.session_state['cached_face_embeddings'] = raw_embs
                                st.success(f"✅ {len(raw_embs)} sampel Face Embedding berhasil disinkronkan untuk pengecekan kemiripan wajah.")
                        except Exception as emb_sync_err:
                            logger.warning(f"Gagal sync face embeddings: {emb_sync_err}")

                # Ambil history dari local DB
                df_hist = db_handler.get_device_bind_history()

                if df_hist.empty:
                    st.info("📭 Belum ada data Device Bind/Unbind History. Klik **Sync History** untuk mengambil dari Google Sheets.")
                else:
                    # ---------- Summary Metrics ----------
                    total_events   = len(df_hist)
                    unique_nrps    = df_hist['NRP'].nunique()
                    unique_devices = df_hist['device_id'].nunique()

                    # Hitung anomali dengan normalisasi Hardware Fingerprint Hash & Status Aktif (Akibat UNBIND)
                    def get_dev_hash(did):
                        if not did or pd.isna(did): return ""
                        d_str = str(did).strip().lower()
                        pts = d_str.split('-')
                        if len(pts) >= 3: return pts[2]
                        elif len(pts) == 2: return pts[1]
                        return d_str

                    # Evaluasi status perangkat aktif saat ini berdasarkan urutan kronologis BIND & UNBIND
                    active_map = {} # (nrp, dev_hash) -> is_active
                    df_sorted_hist = df_hist.sort_values('timestamp', ascending=True) if 'timestamp' in df_hist.columns else df_hist
                    for _, h_row in df_sorted_hist.iterrows():
                        r_nrp = str(h_row.get('NRP', '')).strip()
                        r_did = str(h_row.get('device_id', '')).strip()
                        r_act = str(h_row.get('action', '')).strip().upper()
                        r_hash = get_dev_hash(r_did)
                        if not r_nrp or not r_hash: continue
                        if r_act == 'BIND':
                            active_map[(r_nrp, r_hash)] = True
                        elif r_act == 'UNBIND':
                            active_map[(r_nrp, r_hash)] = False

                    active_rows = [{'NRP': k[0], 'dev_hash': k[1]} for k, v in active_map.items() if v]
                    df_bind = pd.DataFrame(active_rows) if active_rows else pd.DataFrame(columns=['NRP', 'dev_hash'])

                    if not df_bind.empty:
                        nrp_device_counts = (
                            df_bind
                            .groupby('NRP')['dev_hash']
                            .nunique()
                            .reset_index()
                            .rename(columns={'dev_hash': 'device_count'})
                        )
                        device_nrp_counts = (
                            df_bind
                            .groupby('dev_hash')['NRP']
                            .nunique()
                            .reset_index()
                            .rename(columns={'NRP': 'nrp_count'})
                        )
                    else:
                        nrp_device_counts = pd.DataFrame(columns=['NRP', 'device_count'])
                        device_nrp_counts = pd.DataFrame(columns=['dev_hash', 'nrp_count'])

                    high_risk_nrps    = nrp_device_counts[nrp_device_counts['device_count'] >= 3]
                    medium_risk_nrps  = nrp_device_counts[
                        (nrp_device_counts['device_count'] == 2)
                    ]
                    multi_user_devs   = device_nrp_counts[device_nrp_counts['nrp_count'] >= 2]
                    total_suspicious  = len(high_risk_nrps) + len(medium_risk_nrps) + len(multi_user_devs)

                    col_h1, col_h2, col_h3, col_h4 = st.columns(4)
                    col_h1.metric("📋 Total Events", total_events)
                    col_h2.metric("👤 Unique NRP", unique_nrps)
                    col_h3.metric("📱 Unique Devices", unique_devices)
                    col_h4.metric("⚠️ Suspicious Flags", total_suspicious,
                                  delta=f"{total_suspicious} potensi fraud" if total_suspicious > 0 else None,
                                  delta_color="inverse")

                    # ---------- Anomali: 1 NRP → Banyak Device ----------
                    st.markdown("#### ⚠️ Anomali: 1 NRP Menggunakan Banyak Device")
                    if nrp_device_counts[nrp_device_counts['device_count'] >= 2].empty:
                        st.success("✅ Tidak ada anomali NRP menggunakan multiple device.")
                    else:
                        df_nrp_anom = nrp_device_counts[nrp_device_counts['device_count'] >= 2].copy()
                        df_nrp_anom = df_nrp_anom.sort_values('device_count', ascending=False)

                        # Tambahkan nama karyawan
                        if not df_employees.empty:
                            emp_map = df_employees.set_index('NRP')['Nama Staff'].to_dict() if 'Nama Staff' in df_employees.columns else {}
                            df_nrp_anom['Nama Staff'] = df_nrp_anom['NRP'].map(emp_map).fillna('Unknown')
                        else:
                            df_nrp_anom['Nama Staff'] = 'N/A'

                        def risk_label(n):
                            if n >= 3: return "🔴 HIGH RISK"
                            if n == 2: return "🟡 MEDIUM"
                            return "🟢 Normal"

                        df_nrp_anom['Risk Level'] = df_nrp_anom['device_count'].apply(risk_label)

                        st.dataframe(
                            df_nrp_anom[['NRP', 'Nama Staff', 'device_count', 'Risk Level']]
                            .rename(columns={'device_count': 'Jumlah Device Digunakan'}),
                            use_container_width=False
                        )

                        # Drill-down: klik NRP untuk lihat detail
                        sel_nrp = st.selectbox(
                            "🔎 Lihat detail riwayat untuk NRP:",
                            options=['— pilih NRP —'] + df_nrp_anom['NRP'].tolist(),
                            key="fraud_sel_nrp"
                        )
                        if sel_nrp and sel_nrp != '— pilih NRP —':
                            df_detail = db_handler.get_device_bind_history(nrp=sel_nrp)
                            if not df_detail.empty:
                                st.dataframe(
                                    df_detail[['timestamp', 'device_id', 'action', 'triggered_by', 'notes']]
                                    .sort_values('timestamp', ascending=False),
                                    use_container_width=False
                                )

                    st.markdown("#### ⚠️ Anomali: 1 Device Digunakan Oleh Banyak NRP")
                    if multi_user_devs.empty:
                        st.success("✅ Tidak ada anomali device dipakai multiple NRP.")
                    else:
                        df_dev_anom = multi_user_devs.copy().sort_values('nrp_count', ascending=False)
                        st.dataframe(
                            df_dev_anom.rename(columns={'nrp_count': 'Jumlah NRP Menggunakan Device'}),
                            use_container_width=False
                        )

                        sel_dev = st.selectbox(
                            "🔎 Lihat detail riwayat untuk Device ID:",
                            options=['— pilih Device ID —'] + df_dev_anom['device_id'].tolist(),
                            key="fraud_sel_dev"
                        )
                        if sel_dev and sel_dev != '— pilih Device ID —':
                            df_dev_detail = db_handler.get_device_bind_history(device_id=sel_dev)
                            if not df_dev_detail.empty:
                                st.dataframe(
                                    df_dev_detail[['timestamp', 'NRP', 'action', 'triggered_by', 'notes']]
                                    .sort_values('timestamp', ascending=False),
                                    use_container_width=False
                                )

                    # ---------- Anomali: Face Embedding Similarity Check ----------
                    st.markdown("#### 🎭 Deteksi Anomali Kemiripan Wajah (Face Embedding Fraud)")
                    st.caption("Pengecekan kemiripan vektor wajah (*face embedding*) antar karyawan terdaftar untuk mendeteksi potensi duplikasi pendaftaran atau pemalsuan identitas.")

                    # Fetch embeddings from session state or fallback to mappings
                    face_embs = st.session_state.get('cached_face_embeddings')
                    if not face_embs:
                        face_embs = extract_face_vectors_from_mappings(mappings)
                        st.session_state['cached_face_embeddings'] = face_embs

                    col_sim_thresh, col_sim_info = st.columns([3, 1])
                    with col_sim_thresh:
                        thresh_pct = st.slider(
                            "🎯 Pilih Persentase Kemiripan Wajah Minimum (%):",
                            min_value=50,
                            max_value=100,
                            value=80,
                            step=1,
                            key="face_sim_threshold",
                            help="Menampilkan pasangan karyawan yang memiliki persentase kemiripan wajah di atas batas ini."
                        )
                    with col_sim_info:
                        st.metric("👤 Karyawan Teranalisis", len(face_embs) if face_embs else 0)

                    if not face_embs or len(face_embs) < 2:
                        st.info("ℹ️ Diperlukan minimal 2 sampel Face Embedding terdaftar untuk mengecek kemiripan. Klik **🔄 Sync History** di atas untuk mengambil data `Face_Embedding` dari Google Sheets.")
                    else:
                        similar_pairs = compute_face_similarity_pairs(face_embs, df_employees, thresh_pct)
                        if similar_pairs:
                            df_similar = pd.DataFrame(similar_pairs)
                            st.warning(f"⚠️ Terdeteksi **{len(df_similar)}** pasangan karyawan dengan kemiripan wajah ≥ **{thresh_pct}%**!")
                            st.dataframe(
                                df_similar[["NRP 1", "Nama Karyawan 1", "NRP 2", "Nama Karyawan 2", "Kemiripan Wajah (%)", "Tingkat Risiko"]],
                                use_container_width=False
                            )
                        else:
                            st.success(f"✅ Tidak ada indikasi duplikasi/kemiripan wajah melebihi **{thresh_pct}%** dari {len(face_embs)} karyawan terdaftar.")

                    # ---------- Raw Audit Log ----------
                    with st.expander("📜 Lihat Semua Raw Audit Log", expanded=False):
                        # Filter controls
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            filter_action = st.selectbox(
                                "Filter Action:",
                                ['Semua', 'BIND', 'UNBIND', 'UNBIND_REQUEST'],
                                key="hist_filter_action"
                            )
                        with fc2:
                            filter_trigger = st.selectbox(
                                "Filter Triggered By:",
                                ['Semua', 'EMPLOYEE_SELF', 'HR_ADMIN', 'SYSTEM'],
                                key="hist_filter_trigger"
                            )
                        with fc3:
                            search_nrp = st.text_input("Cari NRP:", key="hist_search_nrp", placeholder="SNI...")

                        df_disp = df_hist.copy()
                        if filter_action != 'Semua':
                            df_disp = df_disp[df_disp['action'].str.upper() == filter_action]
                        if filter_trigger != 'Semua':
                            df_disp = df_disp[df_disp['triggered_by'].str.upper() == filter_trigger]
                        if search_nrp.strip():
                            df_disp = df_disp[df_disp['NRP'].str.contains(search_nrp.strip(), case=False, na=False)]

                        st.dataframe(
                            df_disp[['timestamp', 'NRP', 'device_id', 'action', 'triggered_by', 'notes']]
                            .sort_values('timestamp', ascending=False)
                            .reset_index(drop=True),
                            use_container_width=False
                        )
                        st.caption(f"Menampilkan {len(df_disp)} dari {len(df_hist)} total event.")

            except Exception as fraud_err:
                st.error(f"Error memuat Fraud Detection: {fraud_err}")
                logger.error(f"Fraud detection error: {fraud_err}")


        # TAB 6: SETTINGS & RETENTION
        with tab_settings:
           
            # Settings fields
            st.markdown("### 🧹 Database Retention Policy")
            col_ret1, col_ret2 = st.columns(2)
            with col_ret1:
                retention_enabled = st.toggle(
                    "Enable Automatic Log Purging on Sync", 
                    value=st.session_state.retention_enabled, 
                    key="retention_enabled",
                    on_change=on_retention_change
                )
            
            if retention_enabled:
                with col_ret2:
                    retention_months = st.slider(
                        "Retention Duration (Months)", 
                        min_value=1, 
                        max_value=24, 
                        value=st.session_state.retention_months, 
                        key="retention_months",
                        on_change=on_retention_change
                    )
                st.info(f"💡 **Archive & Purge Policy:** All logs older than **{retention_months} months** will be automatically archived as a CSV file to the `data/archive` folder and then deleted from the database upon data synchronization or next login.")

           
            # Admin password change
            st.markdown("---")
            st.markdown("### 🔒 Administrative Account Management")
            
            st.write("**🔑 Change Administrator Password**")
            with st.form("change_password_form", clear_on_submit=True):
                new_pwd = st.text_input("New Password", type="password")
                confirm_pwd = st.text_input("Confirm New Password", type="password")
                pwd_submit = st.form_submit_button("Update Password", use_container_width=False)
                
                if pwd_submit:
                    if not new_pwd:
                        st.error("Password cannot be empty.")
                    elif new_pwd != confirm_pwd:
                        st.error("Passwords do not match.")
                    else:
                        if db_handler.update_admin_password("admin", new_pwd):
                            db_handler.log_admin_action("CHANGE_PASSWORD", "Admin updated account password.")
                            st.success("Password updated successfully!")
                        else:
                            st.error("Failed to update password.")
           
            # Admin log view
            st.markdown("---")
            st.markdown("### 📜 System Administration Logs")
            # st.write(f"Logs recorded inside `{hr_db_path}`:")
            admin_logs = db_handler.get_admin_logs()
            if admin_logs:
                df_admin = pd.DataFrame(admin_logs)
                st.dataframe(df_admin[["id", "timestamp", "action", "details"]], use_container_width=False)
            else:
                st.info("No admin logs found.")
                
if __name__ == "__main__":
    main()
