import os
import io
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import fitz
from PIL import Image

from backend.utils import (
    find_ghostscript,
    is_valid_pdf_signature,
    get_file_size_mb,
    log_job_event,
    TEMP_DIR
)
from backend.jobs import update_job


SETTINGS_MAP = {
    "low": "/prepress",
    "medium": "/ebook",
    "high": "/screen",
}

QUALITY_FALLBACK_MAP = {
    "low": 85,
    "medium": 65,
    "high": 40,
}


def compress_with_pymupdf(input_path: str, output_path: str, level: str = "medium") -> bool:
    """
    Fallback compression engine using PyMuPDF and PIL optimization.
    Used when Ghostscript is not installed on the host system.
    """
    quality = QUALITY_FALLBACK_MAP.get(level.lower(), 65)
    doc = fitz.open(input_path)
    processed_xrefs = set()

    for page_num in range(doc.page_count):
        page = doc[page_num]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref not in processed_xrefs:
                processed_xrefs.add(xref)
                try:
                    img_dict = doc.extract_image(xref)
                    img_bytes = img_dict.get("image")
                    if img_bytes:
                        im = Image.open(io.BytesIO(img_bytes))
                        if im.mode not in ("RGB", "L"):
                            im = im.convert("RGB")
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=quality, optimize=True)
                        doc.update_stream(xref, buf.getvalue())
                except Exception:
                    pass
        del page

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def run_ghostscript_compression(
    job_id: str,
    input_path: str,
    level: str = "medium",
    timeout_seconds: int = 600
) -> Dict[str, Any]:
    """
    Executes PDF compression.
    Primary Engine: Ghostscript (Section 5 specifications).
    Fallback Engine: PyMuPDF image stream optimization.
    """
    start_time = time.time()
    orig_size_mb = get_file_size_mb(input_path)
    
    # 1. Validate PDF signature
    if not is_valid_pdf_signature(input_path):
        err_msg = "Invalid PDF file: The uploaded file does not have a valid PDF header."
        update_job(
            job_id,
            status="error",
            error_message=err_msg,
            original_size_mb=orig_size_mb
        )
        log_job_event(job_id, "compress_error", {"error": err_msg, "file": input_path})
        return {"status": "error", "error_message": err_msg}

    output_filename = f"compressed_{job_id}.pdf"
    output_path = str(TEMP_DIR / output_filename)
    update_job(job_id, status="processing", progress=25)

    # 2. Locate Ghostscript
    gs_exe = find_ghostscript()

    if gs_exe:
        pdf_setting = SETTINGS_MAP.get(level.lower(), "/ebook")
        cmd = [
            gs_exe,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={pdf_setting}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dSAFER",
            f"-sOutputFile={output_path}",
            input_path
        ]

        log_job_event(job_id, "compress_start_gs", {
            "level": level,
            "setting": pdf_setting,
            "orig_size_mb": orig_size_mb,
            "cmd": " ".join(cmd)
        })

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds
            )
            
            if proc.returncode != 0:
                err_output = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
                err_msg = f"Ghostscript error: {err_output}"
                update_job(job_id, status="error", error_message=err_msg, original_size_mb=orig_size_mb)
                log_job_event(job_id, "compress_error", {"returncode": proc.returncode, "error": err_output})
                return {"status": "error", "error_message": err_msg}

        except subprocess.TimeoutExpired:
            err_msg = f"Compression timed out after {timeout_seconds} seconds."
            update_job(job_id, status="error", error_message=err_msg)
            log_job_event(job_id, "compress_timeout", {"timeout_seconds": timeout_seconds})
            return {"status": "error", "error_message": err_msg}
        except Exception as e:
            err_msg = f"Unexpected error during compression: {str(e)}"
            update_job(job_id, status="error", error_message=err_msg)
            return {"status": "error", "error_message": err_msg}

    else:
        # Ghostscript not installed: run PyMuPDF fallback
        log_job_event(job_id, "compress_start_fallback", {
            "level": level,
            "orig_size_mb": orig_size_mb,
            "engine": "pymupdf_fallback"
        })
        try:
            success = compress_with_pymupdf(input_path, output_path, level=level)
            if not success:
                err_msg = "Compression failed to generate an output file."
                update_job(job_id, status="error", error_message=err_msg)
                return {"status": "error", "error_message": err_msg}
        except Exception as e:
            err_msg = f"Compression error: {str(e)}"
            update_job(job_id, status="error", error_message=err_msg)
            return {"status": "error", "error_message": err_msg}

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        err_msg = "Compression completed but failed to produce an output file."
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    compressed_size_mb = get_file_size_mb(output_path)
    download_url = f"/api/compress/download/{job_id}"
    duration = round(time.time() - start_time, 2)

    update_job(
        job_id,
        status="done",
        progress=100,
        original_size_mb=orig_size_mb,
        compressed_size_mb=compressed_size_mb,
        output_path=output_path,
        download_url=download_url
    )
    log_job_event(job_id, "compress_done", {
        "level": level,
        "orig_mb": orig_size_mb,
        "compressed_mb": compressed_size_mb,
        "duration_sec": duration
    })
    
    return {
        "status": "done",
        "progress": 100,
        "original_size_mb": orig_size_mb,
        "compressed_size_mb": compressed_size_mb,
        "download_url": download_url
    }
