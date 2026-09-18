import time
import uuid
import threading
from typing import Dict, Any, Optional
from backend.utils import cleanup_file_safely, cleanup_expired_files

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


def create_job(job_type: str, input_path: Optional[str] = None, original_size_mb: float = 0.0) -> str:
    """Creates a new job and stores initial metadata."""
    cleanup_expired_files()
    job_id = str(uuid.uuid4())
    now = time.time()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "type": job_type,
            "status": "processing",
            "progress": 0,
            "original_size_mb": original_size_mb,
            "compressed_size_mb": None,
            "pages_cleaned": 0,
            "total_pages": 0,
            "download_url": None,
            "error_message": None,
            "input_path": input_path,
            "output_path": None,
            "candidates": [],
            "already_optimal": False,
            "message": None,
            "created_at": now,
            "start_time": now,
            "elapsed_sec": 0,
            "pages_per_sec": 0.0,
        }
    return job_id


def update_job(job_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Updates job fields in a thread-safe manner."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.update(kwargs)
            return job.copy()
    return None


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Returns a copy of the job state with real elapsed time."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        job_copy = job.copy()

    if job_copy.get("status") == "processing":
        elapsed = max(0, int(time.time() - job_copy.get("start_time", job_copy.get("created_at", time.time()))))
        job_copy["elapsed_sec"] = elapsed
        if job_copy.get("type") == "compress":
            # Ghostscript runs as a single subprocess without mid-run page callbacks.
            # Pace progress smoothly based on active time, strictly capped at 95% until complete.
            activity_pct = min(95, max(10, int(elapsed * 4)))
            job_copy["progress"] = max(job_copy.get("progress", 0), activity_pct)

    return job_copy


def remove_job_files(job_id: str):
    """Deletes temporary input and output files associated with the job."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            cleanup_file_safely(job.get("input_path"))
            cleanup_file_safely(job.get("output_path"))
