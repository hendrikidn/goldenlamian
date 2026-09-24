import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Determine the app mode (default to admin)
app_mode = os.getenv("APP_MODE", "admin").strip().lower()

if app_mode == "admin" or app_mode != "employee":
    # Import and run the HR Admin UI
    from ui.HR_Admin_Portal import main as admin_main
    admin_main()
else:
    # Legacy employee mode fallback
    try:
        from ui.Employee_Attendace import main as employee_main
        employee_main()
    except (ImportError, ModuleNotFoundError):
        from ui.HR_Admin_Portal import main as admin_main
        admin_main()

