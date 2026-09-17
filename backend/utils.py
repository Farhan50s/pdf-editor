import os
import glob
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
LOGS_DIR = BACKEND_DIR / "logs"
TEMP_DIR = BACKEND_DIR / "temp"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

JOB_LOG_FILE = LOGS_DIR / "jobs.log"

logger = logging.getLogger("pdf_tool")
logger.setLevel(logging.INFO)
if not logger.handlers:
    c_handler = logging.StreamHandler()
    c_handler.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s"))
    logger.addHandler(c_handler)
    
    f_handler = logging.FileHandler(JOB_LOG_FILE, encoding="utf-8")
    f_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(f_handler)


def find_ghostscript() -> Optional[str]:
    """Finds the Ghostscript console executable on the system."""
    # Common binary names on Windows and Unix
    candidates = ["gswin64c", "gswin32c", "gs"]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
        if os.name == "nt":
            found_exe = shutil.which(f"{name}.exe")
            if found_exe:
                return found_exe

    # Check common Windows directories if not found in PATH
    if os.name == "nt":
        search_patterns = [
            r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
            r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
            r"D:\Program Files\gs\gs*\bin\gswin64c.exe",
            r"D:\gs\*\bin\gswin64c.exe",
            r"C:\gs\*\bin\gswin64c.exe",
            r"D:\anaconda3\Library\bin\gswin64c.exe",
            r"C:\anaconda3\Library\bin\gswin64c.exe",
        ]
        for pattern in search_patterns:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

    return None


def is_valid_pdf_signature(file_path: str | Path) -> bool:
    """Validates if file begins with standard PDF header %PDF-."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(1024)
            return b"%PDF-" in header
    except Exception:
        return False


def get_file_size_mb(file_path: str | Path) -> float:
    """Returns file size in megabytes rounded to 2 decimal places."""
    try:
        size_bytes = os.path.getsize(file_path)
        return round(size_bytes / (1024 * 1024), 2)
    except Exception:
        return 0.0


def log_job_event(job_id: str, action: str, details: dict):
    """
    Appends a structured job log to backend/logs/jobs.log.
    Fields: start time, file size, page count, level/candidate chosen, end time, success/failure.
    """
    timestamp = datetime.now().isoformat()
    msg = f"JOB [{job_id}] | Action: {action} | Time: {timestamp} | Details: {details}"
    logger.info(msg)


def cleanup_expired_files(max_age_seconds: int = 3600):
    """Cleans up temp files older than max_age_seconds (default 1 hour)."""
    now = datetime.now().timestamp()
    try:
        for p in TEMP_DIR.iterdir():
            if p.is_file():
                if now - p.stat().st_mtime > max_age_seconds:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
    except Exception:
        pass


def cleanup_file_safely(file_path: Optional[str | Path]):
    """Safely removes a file if it exists."""
    if file_path:
        try:
            p = Path(file_path)
            if p.exists() and p.is_file():
                p.unlink(missing_ok=True)
        except Exception:
            pass
