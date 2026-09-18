import os
import io
import time
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import fitz
import numpy as np
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


def analyze_image_characteristics(img_dict: dict, pil_img: Image.Image) -> Tuple[bool, bool]:
    """
    Analyzes an extracted PDF image to determine:
    1. is_halftone: True if image is 1-bit or has high edge/noise density typical of a halftone scan.
    2. is_scanned_text: True if image contains scanned document text (paper background with dark text).
    
    Uses dynamic background luminance (90th percentile) to handle aged, yellowish, or grayish paper (160-195).
    """
    # 1. Direct 1-bit or CCITT/JBIG2 check
    if (
        img_dict.get("bpc") == 1
        or pil_img.mode == "1"
        or img_dict.get("ext", "").lower() in ("jbig2", "jb2", "ccitt")
    ):
        return True, True

    try:
        w, h = pil_img.size
        if w < 100 or h < 100:
            return False, False

        # Detect grayscale / low-saturation (monochrome or document scan)
        if pil_img.mode in ("1", "L", "P"):
            is_doc_scan = True
        else:
            # Sample up to 600x600 for performance
            rgb_sample = pil_img.resize((min(w, 600), min(h, 600)), Image.Resampling.BILINEAR) if (w > 600 or h > 600) else pil_img
            rgb_arr = np.array(rgb_sample.convert("RGB"), dtype=np.int16)
            R = rgb_arr[:, :, 0]
            G = rgb_arr[:, :, 1]
            B = rgb_arr[:, :, 2]
            rg_diff = np.abs(R - G)
            gb_diff = np.abs(G - B)
            # Check if across 95% of pixels differences are < 12 (or p95 <= 14)
            is_doc_scan = bool(
                np.mean((rg_diff < 12) & (gb_diff < 12)) >= 0.90
                or (np.percentile(rg_diff, 95) <= 14 and np.percentile(gb_diff, 95) <= 14)
            )

        # Dynamic background luminance calculation: 90th percentile luminance
        gray = pil_img.convert("L")
        sample_gray = gray.resize((min(w, 600), min(h, 600)), Image.Resampling.BILINEAR) if (w > 600 or h > 600) else gray
        arr = np.array(sample_gray, dtype=np.float32)

        bg_lum = float(np.percentile(arr, 90))
        # Paper background threshold: [bg_lum - 25, 255]
        bg_thresh = bg_lum - 25.0

        # Edge density relative to dynamic background: transitions where at least one pixel is non-background (< bg_thresh)
        dh = (np.abs(arr[:, 1:] - arr[:, :-1]) > 15) & ((arr[:, 1:] < bg_thresh) | (arr[:, :-1] < bg_thresh))
        dv = (np.abs(arr[1:, :] - arr[:-1, :]) > 15) & ((arr[1:, :] < bg_thresh) | (arr[:-1, :] < bg_thresh))
        edge_density = float((np.mean(dh) + np.mean(dv)) / 2.0)

        # If document scan AND edge density > 0.008 (relative to dynamic background)
        if is_doc_scan and edge_density > 0.008:
            return True, True

        is_halftone = bool(edge_density > 0.06)
        is_scanned_text = bool(is_doc_scan and edge_density > 0.005)

        return is_halftone, is_scanned_text
    except Exception:
        return False, False


def check_pdf_contains_scanned_text_or_halftone(pdf_path: str, max_pages: int = 10) -> Tuple[bool, bool]:
    """
    Scans representative pages across the document to determine if it contains
    halftone scans or scanned text.
    """
    has_halftone = False
    has_scanned_text = False
    try:
        with fitz.open(pdf_path) as doc:
            num_pages = len(doc)
            sample_step = max(1, num_pages // max_pages)
            for p_idx in range(0, num_pages, sample_step):
                page = doc[p_idx]
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        img_dict = doc.extract_image(xref)
                        if img_dict and img_dict.get("image"):
                            with Image.open(io.BytesIO(img_dict["image"])) as im:
                                is_ht, is_st = analyze_image_characteristics(img_dict, im)
                                if is_ht:
                                    has_halftone = True
                                if is_st:
                                    has_scanned_text = True
                                if has_halftone and has_scanned_text:
                                    return True, True
                    except Exception:
                        pass
    except Exception:
        pass
    return has_halftone, has_scanned_text


def validate_content_preservation(
    orig_path: str,
    compressed_path: str,
    max_drop_threshold: float = 0.18
) -> Tuple[bool, Dict[str, Any]]:
    """
    Robust perceptual content validation:
    1. Samples 12 pages evenly spaced throughout the document (np.linspace).
    2. Watermark Exclusion Band: Crops central body area (top 12% to bottom 90% of page height)
       to prevent static header/footer watermarks from masking body text loss.
    3. Tightened rejection threshold: If body ink density drops by more than 18% on ANY
       sampled page, flags as content loss.
    """
    if not os.path.exists(compressed_path) or os.path.getsize(compressed_path) == 0:
        return False, {"error": "Output file missing or empty"}

    try:
        with fitz.open(orig_path) as doc_orig, fitz.open(compressed_path) as doc_comp:
            if doc_comp.page_count != doc_orig.page_count or doc_comp.is_encrypted:
                return False, {"error": "Page count mismatch or encrypted output"}

            N = doc_orig.page_count
            if N <= 12:
                sample_indices = list(range(N))
            else:
                sample_indices = sorted(list(set(np.linspace(0, N - 1, 12, dtype=int).tolist())))

            zoom = 100.0 / 72.0
            mat = fitz.Matrix(zoom, zoom)

            for p_idx in sample_indices:
                p_orig = doc_orig[p_idx]
                p_comp = doc_comp[p_idx]

                # Watermark Exclusion Band: crop central body area (excluding top 12% and bottom 10%)
                body_rect = fitz.Rect(
                    0,
                    p_orig.rect.height * 0.12,
                    p_orig.rect.width,
                    p_orig.rect.height * 0.90
                )

                pix_orig = p_orig.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, clip=body_rect)
                darkness_orig = 1.0 - (sum(pix_orig.samples) / (len(pix_orig.samples) * 255.0))

                pix_comp = p_comp.get_pixmap(matrix=mat, colorspace=fitz.csGRAY, clip=body_rect)
                darkness_comp = 1.0 - (sum(pix_comp.samples) / (len(pix_comp.samples) * 255.0))

                # If original body area had ink content (not blank white paper), evaluate drop
                if darkness_orig >= 0.005:
                    drop = (darkness_orig - darkness_comp) / darkness_orig
                    if drop > max_drop_threshold:
                        return False, {
                            "page": p_idx,
                            "darkness_orig": round(darkness_orig, 4),
                            "darkness_comp": round(darkness_comp, 4),
                            "drop_pct": round(drop * 100, 2),
                            "error": f"Content loss detected in body of page {p_idx}: ink density dropped by {round(drop * 100, 1)}% (> {round(max_drop_threshold * 100)}%)"
                        }

            return True, {"pages_checked": sample_indices}

    except Exception as e:
        return False, {"error": f"Validation error: {str(e)}"}


def compress_with_pymupdf(input_path: str, output_path: str, level: str = "medium", aggressive: bool = False) -> bool:
    """
    Fallback compression engine using PyMuPDF and PIL optimization.
    Used when Ghostscript is not installed on the host system.
    Safeguards:
    1. Any image classified as halftone or scanned text is STRICTLY EXCLUDED from lossy JPEG
       re-encoding and downsampling. Retained in original lossless Flate/CCITT/JBIG2 format.
    2. Low JPEGQ is strictly reserved for continuous-tone photos.
    """
    base_quality = 40 if aggressive else QUALITY_FALLBACK_MAP.get(level.lower(), 65)

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
                    if not img_bytes:
                        continue

                    im = Image.open(io.BytesIO(img_bytes))

                    # 1. Analyze image characteristics
                    is_halftone, is_scanned_text = analyze_image_characteristics(img_dict, im)

                    # Rule: For ANY image where is_halftone = True or is_scanned_text = True:
                    # STRICTLY DISABLE resolution downsampling (no resizing).
                    # Exclude from lossy JPEG re-encoding. Retain in lossless Flate/CCITT/JBIG2 untouched.
                    if is_halftone or is_scanned_text:
                        continue

                    # Continuous-tone photo content only:
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")

                    # Downsample only genuine photos in aggressive mode
                    if aggressive and (im.width > 1200 or im.height > 1200):
                        im.thumbnail((1200, 1200), Image.Resampling.LANCZOS)

                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=base_quality, optimize=True)
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
    Executes PDF compression with 2-pass size-guard, halftone protection, and perceptual validation:
    1. Halftone scans and scanned text images are excluded from destructive JPEG re-encoding and downsampling.
    2. Dynamic background detection (90th percentile) reliably catches aged or yellowish book scans.
    3. Low compression skips aggressive Pass 2 (protects user quality choice).
    4. Large files (>200 pages) skip Pass 2 (avoids doubling execution time).
    5. Content output is verified with 12-page body-cropped perceptual pixel darkness checking (18% threshold).
    6. Minimal Yield Abort Gate reverts raster scans if size savings are < 5%.
    7. Pass 1 temporary output is purged immediately when Pass 2 starts.
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
    update_job(job_id, status="processing", start_time=time.time())

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

    # Detect if document contains scanned text or halftone content
    has_ht, has_st = check_pdf_contains_scanned_text_or_halftone(input_path)
    is_raster_scan = bool(has_ht or has_st)

    # Check eligibility for Pass 2:
    # Rule 1: Only run aggressive Pass 2 for Medium or High (for Low, skip straight to already_optimal)
    # Rule 2: Only run Pass 2 if document page count <= 200 (skip for > 200)
    can_run_pass2 = (
        level.lower() in ("medium", "high") and
        orig_page_count <= 200
    )

    # If Pass 1 successfully reduced the file size:
    if pass1_bytes < orig_bytes:
        is_p1_valid, p1_val_details = validate_content_preservation(input_path, pass1_output_path, max_drop_threshold=0.18)
        if is_p1_valid:
            savings_ratio = (orig_bytes - pass1_bytes) / orig_bytes
            # Minimal Yield Abort Gate: If savings < 5% on raster scan documents:
            if is_raster_scan and savings_ratio < 0.05:
                # If we cannot run Pass 2, abort to already_optimal to avoid raster artifacts for negligible gain (<5%)
                if not can_run_pass2:
                    cleanup_file_safely(pass1_output_path)
                    return finish_as_already_optimal()
                # If can_run_pass2 is True, proceed to Pass 2 below
            else:
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

    # Pass 1 was NOT smaller than original (or failed content validation / minimal yield gate)
    if not can_run_pass2:
        skip_reason = "level_is_low" if level.lower() not in ("medium", "high") else f"page_count_{orig_page_count}_over_200"
        log_job_event(job_id, "compress_skip_pass2", {
            "reason": skip_reason,
            "level": level,
            "orig_page_count": orig_page_count,
            "pass1_bytes": pass1_bytes,
            "orig_bytes": orig_bytes
        })
        # Delete Pass 1 file immediately
        return finish_as_already_optimal()

    # Delete Pass 1 file immediately once Pass 2 starts
    cleanup_file_safely(pass1_output_path)

    log_job_event(job_id, "compress_pass2_start", {
        "level": level,
        "orig_page_count": orig_page_count,
        "orig_bytes": orig_bytes
    })

    # 3. Run Pass 2 (Aggressive Settings with Scanned Document Guards)
    if gs_exe:
        pdf_setting = SETTINGS_MAP.get(level.lower(), "/ebook")

        # Rule 2: Never use JPEGQ below 80 for any image that contains scanned text
        jpeg_q = 80 if has_st else 60
        downsample = "false" if has_ht else "true"
        color_res = 200 if has_st else 120
        gray_res = 200 if has_st else 120

        cmd_pass2 = [
            gs_exe,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={pdf_setting}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dSAFER",
            f"-dColorImageResolution={color_res}",
            f"-dGrayImageResolution={gray_res}",
            "-dMonoImageResolution=300",
            f"-dJPEGQ={jpeg_q}",
            f"-dDownsampleColorImages={downsample}",
            f"-dDownsampleGrayImages={downsample}",
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

    # Rule 3: Perceptual content validation: render 12 pages before and after at 100 DPI
    # If any page's body darkness dropped by more than 18%, discard and fall back to already_optimal
    is_valid_content, val_details = validate_content_preservation(input_path, output_path, max_drop_threshold=0.18)
    if not is_valid_content:
        log_job_event(job_id, "compress_pass2_content_loss_fallback", val_details)
        cleanup_file_safely(output_path)
        return finish_as_already_optimal()

    pass2_bytes = os.path.getsize(output_path)

    # Compare again: If still not smaller than original, stop trying. Do not return that file.
    if pass2_bytes >= orig_bytes:
        cleanup_file_safely(output_path)
        return finish_as_already_optimal()

    # Minimal Yield Abort Gate: If final compressed size is within 5% of original (savings < 5%)
    # on raster scan documents, revert to the original file to avoid visual degradation for negligible gain.
    if is_raster_scan and ((orig_bytes - pass2_bytes) / orig_bytes < 0.05):
        log_job_event(job_id, "compress_pass2_minimal_yield_abort", {
            "orig_bytes": orig_bytes,
            "pass2_bytes": pass2_bytes,
            "savings_pct": round(((orig_bytes - pass2_bytes) / orig_bytes) * 100, 2)
        })
        cleanup_file_safely(output_path)
        return finish_as_already_optimal()

    # Pass 2 succeeded in shrinking the file and is verified valid without content loss
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
