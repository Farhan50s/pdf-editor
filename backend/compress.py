import os
import io
import time
import shutil
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
    cleanup_file_safely,
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

OPTIMAL_COMPRESSION_MESSAGE = "This file is already efficiently compressed. Compression could not reduce it further."


def compress_with_pymupdf(input_path: str, output_path: str, level: str = "medium", aggressive: bool = False) -> bool:
    """
    Fallback compression engine using PyMuPDF and PIL optimization.
    Used when Ghostscript is not installed on the host system.
    """
    if aggressive:
        quality = 40
    else:
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
                    orig_img_len = len(img_bytes) if img_bytes else 0
                    if img_bytes:
                        im = Image.open(io.BytesIO(img_bytes))
                        if im.mode not in ("RGB", "L"):
                            im = im.convert("RGB")
                        if aggressive and (im.width > 1200 or im.height > 1200):
                            im.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=quality, optimize=True)
                        new_bytes = buf.getvalue()
                        # Only replace stream if the compressed stream is actually smaller
                        if orig_img_len == 0 or len(new_bytes) < orig_img_len:
                            doc.update_stream(xref, new_bytes)
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
    Executes PDF compression with 2-pass size-guard and safety rules:
    1. Low compression skips aggressive Pass 2 (protects user quality choice).
    2. Large files (>200 pages) skip Pass 2 (avoids doubling execution time).
    3. Pass 2 output is verified with fitz.open() to confirm page count and valid structure.
    4. Pass 1 temporary output is purged immediately when Pass 2 starts.
    """
    start_time = time.time()
    orig_bytes = os.path.getsize(input_path)
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

    # Inspect input page count
    orig_page_count = 0
    try:
        with fitz.open(input_path) as in_doc:
            orig_page_count = in_doc.page_count
    except Exception as e:
        err_msg = f"Unable to read PDF structure: {str(e)}"
        update_job(job_id, status="error", error_message=err_msg, original_size_mb=orig_size_mb)
        return {"status": "error", "error_message": err_msg}

    output_filename = f"compressed_{job_id}.pdf"
    output_path = str(TEMP_DIR / output_filename)
    pass1_output_path = str(TEMP_DIR / f"pass1_{job_id}.pdf")
    update_job(job_id, status="processing", progress=25)

    gs_exe = find_ghostscript()

    def finish_as_already_optimal() -> Dict[str, Any]:
        """Safely cleans up temp files and offers the original file for download."""
        cleanup_file_safely(pass1_output_path)
        shutil.copyfile(input_path, output_path)
        duration = round(time.time() - start_time, 2)
        download_url = f"/api/compress/download/{job_id}"

        update_job(
            job_id,
            status="done",
            progress=100,
            original_size_mb=orig_size_mb,
            compressed_size_mb=orig_size_mb,
            output_path=output_path,
            download_url=download_url,
            already_optimal=True,
            message=OPTIMAL_COMPRESSION_MESSAGE
        )
        log_job_event(job_id, "compress_already_optimal", {
            "orig_bytes": orig_bytes,
            "duration_sec": duration
        })
        return {
            "status": "done",
            "progress": 100,
            "original_size_mb": orig_size_mb,
            "compressed_size_mb": orig_size_mb,
            "download_url": download_url,
            "already_optimal": True,
            "message": OPTIMAL_COMPRESSION_MESSAGE
        }

    def is_valid_pdf_result(path_to_check: str) -> bool:
        """Confirms the output file opens cleanly and matches original page count."""
        if not os.path.exists(path_to_check) or os.path.getsize(path_to_check) == 0:
            return False
        try:
            with fitz.open(path_to_check) as chk:
                if chk.page_count != orig_page_count or chk.is_encrypted:
                    return False
            return True
        except Exception:
            return False

    # 2. Run Pass 1
    if gs_exe:
        pdf_setting = SETTINGS_MAP.get(level.lower(), "/ebook")
        cmd_pass1 = [
            gs_exe,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={pdf_setting}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dSAFER",
            f"-sOutputFile={pass1_output_path}",
            input_path
        ]

        log_job_event(job_id, "compress_start_gs_pass1", {
            "level": level,
            "setting": pdf_setting,
            "orig_size_mb": orig_size_mb,
            "orig_bytes": orig_bytes,
            "orig_page_count": orig_page_count,
            "cmd": " ".join(cmd_pass1)
        })

        try:
            proc = subprocess.run(
                cmd_pass1,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds
            )
            
            if proc.returncode != 0:
                err_output = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
                err_msg = f"Ghostscript error: {err_output}"
                cleanup_file_safely(pass1_output_path)
                update_job(job_id, status="error", error_message=err_msg, original_size_mb=orig_size_mb)
                log_job_event(job_id, "compress_error", {"returncode": proc.returncode, "error": err_output})
                return {"status": "error", "error_message": err_msg}

        except subprocess.TimeoutExpired:
            cleanup_file_safely(pass1_output_path)
            err_msg = f"Compression timed out after {timeout_seconds} seconds."
            update_job(job_id, status="error", error_message=err_msg)
            log_job_event(job_id, "compress_timeout", {"timeout_seconds": timeout_seconds})
            return {"status": "error", "error_message": err_msg}
        except Exception as e:
            cleanup_file_safely(pass1_output_path)
            err_msg = f"Unexpected error during compression: {str(e)}"
            update_job(job_id, status="error", error_message=err_msg)
            return {"status": "error", "error_message": err_msg}

    else:
        # Fallback engine Pass 1
        log_job_event(job_id, "compress_start_fallback_pass1", {
            "level": level,
            "orig_size_mb": orig_size_mb,
            "orig_bytes": orig_bytes,
            "orig_page_count": orig_page_count,
            "engine": "pymupdf_fallback"
        })
        try:
            success = compress_with_pymupdf(input_path, pass1_output_path, level=level, aggressive=False)
            if not success:
                cleanup_file_safely(pass1_output_path)
                err_msg = "Compression failed to generate an output file."
                update_job(job_id, status="error", error_message=err_msg)
                return {"status": "error", "error_message": err_msg}
        except Exception as e:
            cleanup_file_safely(pass1_output_path)
            err_msg = f"Compression error: {str(e)}"
            update_job(job_id, status="error", error_message=err_msg)
            return {"status": "error", "error_message": err_msg}

    # Evaluate Pass 1 result
    pass1_bytes = os.path.getsize(pass1_output_path) if os.path.exists(pass1_output_path) else float('inf')

    # If Pass 1 successfully reduced the file size:
    if pass1_bytes < orig_bytes and is_valid_pdf_result(pass1_output_path):
        if os.path.exists(output_path):
            cleanup_file_safely(output_path)
        shutil.move(pass1_output_path, output_path)
        final_comp_size_mb = get_file_size_mb(output_path)
        download_url = f"/api/compress/download/{job_id}"
        duration = round(time.time() - start_time, 2)

        update_job(
            job_id,
            status="done",
            progress=100,
            original_size_mb=orig_size_mb,
            compressed_size_mb=final_comp_size_mb,
            output_path=output_path,
            download_url=download_url,
            already_optimal=False,
            message=None
        )
        log_job_event(job_id, "compress_done_pass1", {
            "level": level,
            "orig_mb": orig_size_mb,
            "compressed_mb": final_comp_size_mb,
            "orig_bytes": orig_bytes,
            "compressed_bytes": pass1_bytes,
            "duration_sec": duration
        })
        return {
            "status": "done",
            "progress": 100,
            "original_size_mb": orig_size_mb,
            "compressed_size_mb": final_comp_size_mb,
            "download_url": download_url,
            "already_optimal": False,
            "message": None
        }

    # Pass 1 was NOT smaller than original (or was invalid)
    # Check eligibility for Pass 2:
    # Rule 1: Only run aggressive Pass 2 for Medium or High (for Low, skip straight to already_optimal)
    # Rule 2: Only run Pass 2 if document page count <= 200 (skip for > 200)
    can_run_pass2 = (
        level.lower() in ("medium", "high") and
        orig_page_count <= 200
    )

    if not can_run_pass2:
        skip_reason = "level_is_low" if level.lower() not in ("medium", "high") else f"page_count_{orig_page_count}_over_200"
        log_job_event(job_id, "compress_skip_pass2", {
            "reason": skip_reason,
            "level": level,
            "orig_page_count": orig_page_count,
            "pass1_bytes": pass1_bytes,
            "orig_bytes": orig_bytes
        })
        # Rule 4: Delete Pass 1 file immediately
        return finish_as_already_optimal()

    # Rule 4: Delete Pass 1 file immediately once Pass 2 starts
    cleanup_file_safely(pass1_output_path)

    log_job_event(job_id, "compress_pass2_start", {
        "level": level,
        "orig_page_count": orig_page_count,
        "orig_bytes": orig_bytes
    })

    # 3. Run Pass 2 (Aggressive Settings)
    if gs_exe:
        pdf_setting = SETTINGS_MAP.get(level.lower(), "/ebook")
        cmd_pass2 = [
            gs_exe,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={pdf_setting}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dSAFER",
            "-dColorImageResolution=120",
            "-dGrayImageResolution=120",
            "-dMonoImageResolution=300",
            "-dJPEGQ=60",
            "-dDownsampleColorImages=true",
            "-dDownsampleGrayImages=true",
            f"-sOutputFile={output_path}",
            input_path
        ]
        try:
            proc2 = subprocess.run(
                cmd_pass2,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds
            )
            if proc2.returncode != 0:
                log_job_event(job_id, "compress_pass2_warning", {
                    "returncode": proc2.returncode,
                    "stderr": proc2.stderr.strip()
                })
        except Exception as e:
            log_job_event(job_id, "compress_pass2_exception", {"error": str(e)})
    else:
        # Fallback engine aggressive
        try:
            compress_with_pymupdf(input_path, output_path, level=level, aggressive=True)
        except Exception as e:
            log_job_event(job_id, "compress_fallback_pass2_exception", {"error": str(e)})

    # Rule 3: After Pass 2 finishes, open result with fitz.open().
    # Confirm it opens without error and has same page count. If corrupt, fall back to already_optimal with original.
    if not is_valid_pdf_result(output_path):
        log_job_event(job_id, "compress_pass2_invalid_fallback", {
            "error": "Pass 2 output failed validation check (corrupt or page count mismatch)"
        })
        cleanup_file_safely(output_path)
        return finish_as_already_optimal()

    pass2_bytes = os.path.getsize(output_path)

    # Compare again: If still not smaller than original, stop trying. Do not return that file.
    if pass2_bytes >= orig_bytes:
        cleanup_file_safely(output_path)
        return finish_as_already_optimal()

    # Pass 2 succeeded in shrinking the file and is verified valid
    final_comp_size_mb = get_file_size_mb(output_path)
    download_url = f"/api/compress/download/{job_id}"
    duration = round(time.time() - start_time, 2)

    update_job(
        job_id,
        status="done",
        progress=100,
        original_size_mb=orig_size_mb,
        compressed_size_mb=final_comp_size_mb,
        output_path=output_path,
        download_url=download_url,
        already_optimal=False,
        message=None
    )
    log_job_event(job_id, "compress_done_pass2", {
        "level": level,
        "orig_mb": orig_size_mb,
        "compressed_mb": final_comp_size_mb,
        "orig_bytes": orig_bytes,
        "compressed_bytes": pass2_bytes,
        "duration_sec": duration
    })
    
    return {
        "status": "done",
        "progress": 100,
        "original_size_mb": orig_size_mb,
        "compressed_size_mb": final_comp_size_mb,
        "download_url": download_url,
        "already_optimal": False,
        "message": None
    }
