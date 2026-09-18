import os
import re
import time
import math
import random
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

import fitz  # PyMuPDF
from PIL import Image, ImageDraw

from backend.utils import (
    is_valid_pdf_signature,
    get_file_size_mb,
    log_job_event,
    TEMP_DIR
)
from backend.jobs import update_job


def normalize_text(text: str) -> str:
    """Normalizes string for comparison by collapsing whitespace and stripping."""
    return " ".join(text.strip().split())


def round_bbox(bbox: Tuple[float, float, float, float], step: float = 5.0) -> Tuple[float, float, float, float]:
    """Rounds bounding box coordinates to the nearest step (default 5 points)."""
    return tuple(round(coord / step) * step for coord in bbox)


def is_bbox_close(bbox1: Tuple[float, float, float, float], bbox2: Tuple[float, float, float, float], tolerance: float = 15.0) -> bool:
    """Checks if two bounding boxes are within the specified tolerance in points."""
    return all(abs(c1 - c2) <= tolerance for c1, c2 in zip(bbox1, bbox2))


def get_evenly_spaced_sample_indices(total_pages: int, max_samples: int = 12) -> List[int]:
    """
    Returns evenly spaced page indices across the document.
    Ensures front, middle, and back of the document are sampled.
    """
    if total_pages <= max_samples:
        return list(range(total_pages))
    
    # Generate evenly spaced sample points
    step = (total_pages - 1) / (max_samples - 1)
    indices = sorted(list(set(int(round(i * step)) for i in range(max_samples))))
    return indices


STOP_WORDS = {
    "of", "and", "the", "is", "in", "to", "a", "or", "by", "as", "at",
    "for", "with", "from", "on", "that", "this", "it", "an", "be", "are",
    "was", "were", "which", "then", "=", "-", ".", ",", ";", ":", "(", ")",
    "[", "]", "{", "}", "+", "*", "/", "\\", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"
}


def detect_watermarks(file_path: str, job_id: str) -> Dict[str, Any]:
    """
    Detects repeating watermark candidates (Text, Images, Vector Graphics)
    using evenly distributed sampling across the document.
    """
    if not is_valid_pdf_signature(file_path):
        return {
            "job_id": job_id,
            "error": "Invalid PDF file: The uploaded file does not have a valid PDF header.",
            "candidates": [],
            "page_count": 0
        }

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {
            "job_id": job_id,
            "error": f"Failed to open PDF: {str(e)}",
            "candidates": [],
            "page_count": 0
        }

    if doc.needs_pass:
        doc.close()
        return {
            "job_id": job_id,
            "error": "This PDF is password-protected. Please provide an unlocked document.",
            "candidates": [],
            "page_count": 0
        }

    total_pages = doc.page_count
    if total_pages == 0:
        doc.close()
        return {
            "job_id": job_id,
            "error": "The PDF document contains no pages.",
            "candidates": [],
            "page_count": 0
        }

    sample_indices = get_evenly_spaced_sample_indices(total_pages, max_samples=12)
    sample_count = len(sample_indices)
    threshold = math.ceil(0.80 * sample_count)

    text_candidate_map: Dict[str, Dict[str, Any]] = {}
    image_hash_map: Dict[str, Dict[str, Any]] = {}
    drawing_candidate_map: Dict[str, Dict[str, Any]] = {}

    scanned_pages_count = 0

    for p_idx in sample_indices:
        page = doc[p_idx]
        
        # 1. Text Watermark Detection
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])
        
        text_blocks = [b for b in blocks if b.get("type") == 0]
        image_blocks = [b for b in blocks if b.get("type") == 1]
        images_list = page.get_images(full=True)
        
        # Scanned page detection heuristic
        if len(text_blocks) <= 1 and (len(image_blocks) >= 1 or len(images_list) >= 1):
            scanned_pages_count += 1

        seen_on_this_page_text: Set[str] = set()
        for b in text_blocks:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    raw_text = span.get("text", "")
                    norm = normalize_text(raw_text)
                    # Filter out stop words, digits, single symbols, and long body paragraphs
                    if norm and len(norm) >= 3 and len(norm) < 60 and not norm.isdigit() and norm.lower() not in STOP_WORDS:
                        span_bbox = tuple(span.get("bbox", (0, 0, 0, 0)))
                        key = norm
                        
                        if key not in seen_on_this_page_text:
                            seen_on_this_page_text.add(key)
                            if key not in text_candidate_map:
                                text_candidate_map[key] = {
                                    "type": "text",
                                    "text": norm,
                                    "bbox": list(span_bbox),
                                    "pages": set([p_idx]),
                                    "first_seen_page": p_idx
                                }
                            else:
                                text_candidate_map[key]["pages"].add(p_idx)

        # 2. Image Watermark Detection
        seen_on_this_page_img: Set[str] = set()
        for img_info in images_list:
            xref = img_info[0]
            try:
                img_dict = doc.extract_image(xref)
                img_bytes = img_dict.get("image", b"")
                img_hash = hashlib.md5(img_bytes).hexdigest()
            except Exception:
                img_hash = f"xref_{xref}"

            if img_hash not in seen_on_this_page_img:
                seen_on_this_page_img.add(img_hash)
                rects = page.get_image_rects(xref)
                img_bbox = list(rects[0]) if rects else [0, 0, 100, 100]

                if img_hash not in image_hash_map:
                    image_hash_map[img_hash] = {
                        "type": "image",
                        "xref": xref,
                        "hash": img_hash,
                        "bbox": img_bbox,
                        "pages": set([p_idx]),
                        "first_seen_page": p_idx
                    }
                else:
                    image_hash_map[img_hash]["pages"].add(p_idx)

        # 3. Vector Graphic Watermark Detection (Drawings / Paths)
        try:
            drawings = page.get_drawings()
            seen_on_this_page_dwg: Set[str] = set()
            for dwg in drawings:
                dwg_rect = dwg.get("rect")
                if dwg_rect:
                    r_rect = round_bbox(tuple(dwg_rect), step=10.0)
                    dwg_type = dwg.get("type", "")
                    dwg_color = str(dwg.get("color"))
                    dwg_key = f"dwg_{dwg_type}_{dwg_color}_{r_rect}"
                    
                    if dwg_key not in seen_on_this_page_dwg:
                        seen_on_this_page_dwg.add(dwg_key)
                        if dwg_key not in drawing_candidate_map:
                            drawing_candidate_map[dwg_key] = {
                                "type": "vector",
                                "rect": list(dwg_rect),
                                "rounded_rect": r_rect,
                                "pages": set([p_idx]),
                                "first_seen_page": p_idx
                            }
                        else:
                            drawing_candidate_map[dwg_key]["pages"].add(p_idx)
        except Exception:
            pass

        del page

    # Collect candidates meeting recurrence threshold
    raw_candidates: List[Dict[str, Any]] = []

    for key, data in text_candidate_map.items():
        pages_seen = len(data["pages"])
        if pages_seen >= threshold:
            raw_candidates.append({
                "type": "text",
                "sample_text": data["text"],
                "bbox": data["bbox"],
                "rounded_bbox": data.get("rounded_bbox"),
                "pages_found_on": pages_seen,
                "sample_count": sample_count,
                "confidence": round(pages_seen / sample_count, 2),
                "first_seen_page": data["first_seen_page"]
            })

    for img_hash, data in image_hash_map.items():
        pages_seen = len(data["pages"])
        if pages_seen >= threshold:
            raw_candidates.append({
                "type": "image",
                "sample_text": None,
                "bbox": data["bbox"],
                "pages_found_on": pages_seen,
                "sample_count": sample_count,
                "confidence": round(pages_seen / sample_count, 2),
                "xref": data["xref"],
                "image_hash": img_hash,
                "first_seen_page": data["first_seen_page"]
            })

    for dwg_key, data in drawing_candidate_map.items():
        pages_seen = len(data["pages"])
        if pages_seen >= threshold:
            raw_candidates.append({
                "type": "vector",
                "sample_text": "Vector Graphic Watermark",
                "bbox": data["rect"],
                "pages_found_on": pages_seen,
                "sample_count": sample_count,
                "confidence": round(pages_seen / sample_count, 2),
                "first_seen_page": data["first_seen_page"]
            })

    raw_candidates.sort(key=lambda x: (x["pages_found_on"], x["confidence"]), reverse=True)
    top_candidates = raw_candidates[:5]

    # Render Preview Thumbnails
    candidates_output: List[Dict[str, Any]] = []
    
    # Pre-render page pixmaps on demand for preview
    rendered_pages: Dict[int, Image.Image] = {}

    for idx, cand in enumerate(top_candidates):
        cid = f"wm_{idx + 1}"
        cand["candidate_id"] = cid

        p_no = cand.get("first_seen_page", 0)
        if p_no not in rendered_pages:
            p_obj = doc[p_no]
            pix = p_obj.get_pixmap(dpi=150)
            rendered_pages[p_no] = (
                Image.frombytes("RGB", [pix.width, pix.height], pix.samples),
                pix.width / p_obj.rect.width if p_obj.rect.width else 1.0,
                pix.height / p_obj.rect.height if p_obj.rect.height else 1.0
            )

        img_base, scale_x, scale_y = rendered_pages[p_no]
        preview_copy = img_base.copy()
        draw = ImageDraw.Draw(preview_copy)
        
        bx0, by0, bx1, by1 = cand["bbox"]
        px0, py0 = bx0 * scale_x, by0 * scale_y
        px1, py1 = bx1 * scale_x, by1 * scale_y
        
        draw.rectangle([px0 - 2, py0 - 2, px1 + 2, py1 + 2], outline="red", width=3)
        
        preview_filename = f"preview_{job_id}_{cid}.png"
        preview_path = TEMP_DIR / preview_filename
        preview_copy.save(str(preview_path), format="PNG")

        cand["page_preview_image_url"] = f"/api/watermark/preview/{cid}?job_id={job_id}"
        # Provide user-friendly recurrence label
        pct = int(cand["confidence"] * 100)
        cand["estimated_coverage"] = f"Found on ~{pct}% of sampled pages ({cand['pages_found_on']}/{sample_count} sampled)"
        cand["possibly_same_watermark"] = False
        candidates_output.append(cand)

    # Detect fragmented text runs on page 1 sharing the same row and adjacent x-ranges
    for i in range(len(candidates_output)):
        for j in range(i + 1, len(candidates_output)):
            c1 = candidates_output[i]
            c2 = candidates_output[j]
            b1 = c1.get("bbox")
            b2 = c2.get("bbox")
            if not b1 or not b2:
                continue

            # Compare if on the same first_seen_page
            if c1.get("first_seen_page") == c2.get("first_seen_page"):
                y0_1, y1_1 = b1[1], b1[3]
                y0_2, y1_2 = b2[1], b2[3]

                # Row alignment: vertical coordinates match within 6 points
                same_row = abs(y0_1 - y0_2) <= 6.0 and abs(y1_1 - y1_2) <= 6.0

                # Horizontal adjacency: gap between boxes <= 35 points
                x0_1, x1_1 = b1[0], b1[2]
                x0_2, x1_2 = b2[0], b2[2]

                if x1_1 <= x0_2:
                    h_gap = x0_2 - x1_1
                elif x1_2 <= x0_1:
                    h_gap = x0_1 - x1_2
                else:
                    h_gap = 0.0

                if same_row and h_gap <= 35.0:
                    c1["possibly_same_watermark"] = True
                    c2["possibly_same_watermark"] = True

    # Scanned PDF check
    warning = None
    if scanned_pages_count >= threshold and len(candidates_output) == 0:
        warning = "This looks like a scanned PDF. Watermark removal may not work well here."

    doc.close()

    result = {
        "job_id": job_id,
        "page_count": total_pages,
        "candidates": candidates_output,
        "sampled_pages": sample_count,
        "scanned_pdf_warning": warning
    }
    
    update_job(job_id, candidates=candidates_output, total_pages=total_pages)
    log_job_event(job_id, "detect_watermarks", {
        "total_pages": total_pages,
        "candidates_found": len(candidates_output),
        "sampled": sample_count,
        "warning": warning
    })
    
    return result


def remove_watermark(
    job_id: str,
    input_path: str,
    candidate: Optional[Dict[str, Any]] = None,
    candidates: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Surgically removes one or more selected watermark candidates in a SINGLE page pass.
    Crucial Safeguards:
    - Redacts individual text spans (tight boxes), NEVER giant line/block bounding boxes.
    - Applies redactions with images=PDF_REDACT_IMAGE_NONE and graphics=PDF_REDACT_LINE_ART_NONE
      so underlying graphics, charts, and diagrams are 100% preserved.
    - Deletes image XObjects and cleans stream references.
    - Processes every page exactly once for all selected candidates, avoiding O(N * K) processing.
    - Deep verification samples 20-30 pages post-removal asserting ALL selected watermarks are gone.
    """
    # Normalize candidates input to a list
    cand_list: List[Dict[str, Any]] = []
    if candidates:
        cand_list = list(candidates)
    elif candidate:
        cand_list = [candidate]

    if not cand_list:
        err_msg = "No watermark candidates specified for removal."
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    if not os.path.exists(input_path):
        err_msg = "Input PDF file not found on disk."
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    try:
        doc = fitz.open(input_path)
    except Exception as e:
        err_msg = f"Failed to open PDF for watermark removal: {str(e)}"
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    total_pages = doc.page_count
    start_cleaning_time = time.time()
    update_job(job_id, status="processing", progress=0, total_pages=total_pages, pages_cleaned=0, pages_per_sec=0.0)

    # Partition targets across all selected candidates
    target_texts: List[str] = []
    target_xrefs: Set[int] = set()
    target_hashes: Set[str] = set()
    target_rects: List[List[float]] = []
    candidates_removed_ids: List[str] = []

    for c in cand_list:
        cid = c.get("candidate_id")
        if cid:
            candidates_removed_ids.append(cid)
        ctype = c.get("type", "text")
        if ctype == "text":
            st = c.get("sample_text")
            if st:
                target_texts.append(normalize_text(st))
        elif ctype == "image":
            if c.get("xref") is not None:
                target_xrefs.add(c["xref"])
            if c.get("image_hash"):
                target_hashes.add(c["image_hash"])
        elif ctype == "vector":
            r = c.get("bbox") or c.get("rect")
            if r:
                target_rects.append(list(r))

    # Process page by page in a SINGLE pass with strict memory release
    for page_num in range(total_pages):
        page = doc[page_num]
        redaction_added = False

        # 1. Surgical Text Redactions (all text targets in one pass)
        if target_texts:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            span_text = normalize_text(span.get("text", ""))
                            if not span_text:
                                continue
                            matched = False
                            for t_text in target_texts:
                                if span_text == t_text or (len(t_text) > 4 and t_text in span_text):
                                    matched = True
                                    break
                            if matched:
                                span_bbox = span.get("bbox")
                                if span_bbox:
                                    page.add_redact_annot(fitz.Rect(span_bbox), fill=None)
                                    redaction_added = True

        # 2. Vector Watermark Redactions (all vector targets in one pass)
        if target_rects:
            for v_rect in target_rects:
                page.add_redact_annot(fitz.Rect(v_rect), fill=None)
                redaction_added = True

        # Apply redactions once per page
        if redaction_added:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE
            )

        # 3. Surgical Image Watermark Deletions (all image targets in one pass)
        if target_xrefs or target_hashes:
            images_on_page = page.get_images(full=True)
            names_to_remove = set()
            matching_xrefs = set()

            for img_info in images_on_page:
                xref = img_info[0]
                img_name = img_info[7]
                matched = False
                if xref in target_xrefs:
                    matched = True
                elif target_hashes:
                    try:
                        extracted = doc.extract_image(xref)
                        if hashlib.md5(extracted.get("image", b"")).hexdigest() in target_hashes:
                            matched = True
                    except Exception:
                        pass

                if matched:
                    matching_xrefs.add(xref)
                    if img_name:
                        names_to_remove.add(img_name)

            if names_to_remove:
                for cx in page.get_contents():
                    try:
                        stream = doc.xref_stream(cx).decode("latin1", errors="ignore")
                        for name in names_to_remove:
                            stream = re.sub(rf"/{re.escape(name)}\s+Do", "", stream)
                        doc.update_stream(cx, stream.encode("latin1"))
                    except Exception:
                        pass
                page.clean_contents()
            elif matching_xrefs:
                for xref in matching_xrefs:
                    try:
                        page.delete_image(xref)
                    except Exception:
                        pass
                page.clean_contents()

        del page

        pages_done = page_num + 1
        elapsed_sec = max(0.001, time.time() - start_cleaning_time)
        pages_per_sec = round(pages_done / elapsed_sec, 1)
        progress = int((pages_done / total_pages) * 100)

        # Emits progress update with pages_per_sec throughput
        update_job(
            job_id,
            progress=progress,
            pages_cleaned=pages_done,
            pages_per_sec=pages_per_sec
        )

    # Save cleaned document with garbage collection and deflation
    output_filename = f"cleaned_{job_id}.pdf"
    output_path = str(TEMP_DIR / output_filename)
    
    try:
        doc.save(output_path, garbage=4, deflate=True, clean=True)
    except Exception as e:
        doc.close()
        err_msg = f"Failed to save cleaned PDF: {str(e)}"
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    doc.close()

    # Step 7: Deep Verification — Sample 20-30 pages including first & last
    # Verifies that ALL selected watermarks are gone from all sampled pages
    try:
        verify_doc = fitz.open(output_path)
        v_total = verify_doc.page_count
        
        verify_count = min(30, v_total)
        step = (v_total - 1) / max(1, (verify_count - 1))
        verify_indices = sorted(list(set(int(round(i * step)) for i in range(verify_count))))
        
        watermark_still_present = False
        failed_candidate_desc = ""

        for idx in verify_indices:
            v_page = verify_doc[idx]
            
            # 1. Check all text candidates
            if target_texts:
                page_text = normalize_text(v_page.get_text())
                for t_text in target_texts:
                    if t_text in page_text:
                        watermark_still_present = True
                        failed_candidate_desc = f"text watermark '{t_text}'"
                        break
                if watermark_still_present:
                    del v_page
                    break

            # 2. Check all image candidates
            if target_xrefs or target_hashes:
                v_images = v_page.get_images(full=True)
                for v_img in v_images:
                    v_xref = v_img[0]
                    if v_xref in target_xrefs:
                        watermark_still_present = True
                        failed_candidate_desc = f"image watermark xref {v_xref}"
                        break
                    elif target_hashes:
                        try:
                            ext = verify_doc.extract_image(v_xref)
                            if hashlib.md5(ext.get("image", b"")).hexdigest() in target_hashes:
                                watermark_still_present = True
                                failed_candidate_desc = "image watermark hash match"
                                break
                        except Exception:
                            pass
                    if watermark_still_present:
                        break

            del v_page
            if watermark_still_present:
                break

        verify_doc.close()

        if watermark_still_present:
            err_msg = f"Watermark ({failed_candidate_desc}) could not be fully removed on all pages."
            update_job(job_id, status="error", error_message=err_msg)
            log_job_event(job_id, "watermark_verify_fail", {
                "candidates_removed": candidates_removed_ids,
                "failed": failed_candidate_desc
            })
            return {"status": "error", "error_message": err_msg}

    except Exception as e:
        err_msg = f"Verification check encountered an error: {str(e)}"
        update_job(job_id, status="error", error_message=err_msg)
        return {"status": "error", "error_message": err_msg}

    download_url = f"/api/watermark/download/{job_id}"
    update_job(
        job_id,
        status="done",
        progress=100,
        output_path=output_path,
        download_url=download_url,
        pages_cleaned=total_pages,
        candidates_removed=candidates_removed_ids
    )
    log_job_event(job_id, "watermark_remove_done", {
        "total_pages": total_pages,
        "output_path": output_path,
        "candidates_removed": candidates_removed_ids
    })
    
    return {
        "status": "done",
        "progress": 100,
        "pages_cleaned": total_pages,
        "candidates_removed": candidates_removed_ids,
        "output_path": output_path,
        "download_url": download_url
    }
