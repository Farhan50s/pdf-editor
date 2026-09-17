import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.utils import (
    BASE_DIR,
    TEMP_DIR,
    get_file_size_mb,
    is_valid_pdf_signature,
    cleanup_file_safely,
    find_ghostscript
)
from backend.jobs import (
    create_job,
    get_job,
    update_job,
    remove_job_files
)
from backend.compress import run_ghostscript_compression
from backend.watermark import detect_watermarks, remove_watermark

app = FastAPI(title="PDF Tool API", description="Local PDF Compression & Watermark Removal Tool")

FRONTEND_DIR = BASE_DIR / "frontend"


class WatermarkRemoveRequest(BaseModel):
    job_id: str
    candidate_id: str


@app.get("/api/system/status")
async def system_status():
    """Checks system dependencies status like Ghostscript."""
    gs_path = find_ghostscript()
    return {
        "ghostscript_available": bool(gs_path),
        "ghostscript_path": gs_path
    }


# ==========================================
# Feature 1: PDF Compression Endpoints
# ==========================================

@app.post("/api/compress")
async def compress_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    level: str = Form("medium")
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are supported.")

    temp_input_filename = f"upload_compress_{file.filename}"
    temp_input_path = str(TEMP_DIR / temp_input_filename)

    # Save uploaded file
    try:
        with open(temp_input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {str(e)}")

    if not is_valid_pdf_signature(temp_input_path):
        cleanup_file_safely(temp_input_path)
        raise HTTPException(status_code=400, detail="Invalid PDF file: Missing or invalid PDF signature header.")

    orig_size_mb = get_file_size_mb(temp_input_path)
    job_id = create_job("compress", input_path=temp_input_path, original_size_mb=orig_size_mb)

    # Launch background job
    background_tasks.add_task(
        run_ghostscript_compression,
        job_id=job_id,
        input_path=temp_input_path,
        level=level
    )

    return {"job_id": job_id}


@app.get("/api/compress/status/{job_id}")
async def compress_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Compression job not found.")
    return {
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "original_size_mb": job.get("original_size_mb", 0.0),
        "compressed_size_mb": job.get("compressed_size_mb"),
        "download_url": job.get("download_url"),
        "error_message": job.get("error_message")
    }


@app.get("/api/compress/download/{job_id}")
async def compress_download(job_id: str, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    output_path = job.get("output_path")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="Compressed file not found or expired.")

    # Schedule cleanup after download
    background_tasks.add_task(remove_job_files, job_id=job_id)

    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=f"compressed_{job_id[:8]}.pdf"
    )


# ==========================================
# Feature 2: PDF Watermark Removal Endpoints
# ==========================================

@app.post("/api/watermark/detect")
async def watermark_detect(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are supported.")

    temp_input_filename = f"upload_wm_{file.filename}"
    temp_input_path = str(TEMP_DIR / temp_input_filename)

    try:
        with open(temp_input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {str(e)}")

    if not is_valid_pdf_signature(temp_input_path):
        cleanup_file_safely(temp_input_path)
        raise HTTPException(status_code=400, detail="Invalid PDF file: Missing or invalid PDF signature header.")

    orig_size_mb = get_file_size_mb(temp_input_path)
    job_id = create_job("watermark_detect", input_path=temp_input_path, original_size_mb=orig_size_mb)

    result = detect_watermarks(temp_input_path, job_id=job_id)
    if "error" in result:
        cleanup_file_safely(temp_input_path)
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@app.get("/api/watermark/preview/{candidate_id}")
async def watermark_preview(candidate_id: str, job_id: Optional[str] = Query(None)):
    # Look for matching preview file in temp
    if job_id:
        preview_file = TEMP_DIR / f"preview_{job_id}_{candidate_id}.png"
    else:
        # Match candidate id across any preview file
        matches = list(TEMP_DIR.glob(f"preview_*_{candidate_id}.png"))
        preview_file = matches[0] if matches else None

    if not preview_file or not preview_file.exists():
        raise HTTPException(status_code=404, detail="Preview image not found.")

    return FileResponse(str(preview_file), media_type="image/png")


@app.post("/api/watermark/remove")
async def watermark_remove(req: WatermarkRemoveRequest, background_tasks: BackgroundTasks):
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Watermark job not found.")

    candidates = job.get("candidates", [])
    matching_candidate = next((c for c in candidates if c.get("candidate_id") == req.candidate_id), None)
    if not matching_candidate:
        raise HTTPException(status_code=400, detail=f"Candidate {req.candidate_id} not found in this job.")

    input_path = job.get("input_path")
    if not input_path or not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail="Original uploaded PDF not found or expired.")

    # Create new job ID for tracking the removal process
    remove_job_id = create_job("watermark_remove", input_path=input_path, original_size_mb=job.get("original_size_mb", 0.0))

    # Background task for removal
    background_tasks.add_task(
        remove_watermark,
        job_id=remove_job_id,
        input_path=input_path,
        candidate=matching_candidate
    )

    return {"job_id": remove_job_id}


@app.get("/api/watermark/status/{job_id}")
async def watermark_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Watermark removal job not found.")
    return {
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "pages_cleaned": job.get("pages_cleaned", 0),
        "download_url": job.get("download_url"),
        "error_message": job.get("error_message")
    }


@app.get("/api/watermark/download/{job_id}")
async def watermark_download(job_id: str, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    output_path = job.get("output_path")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="Cleaned PDF not found or expired.")

    background_tasks.add_task(remove_job_files, job_id=job_id)

    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=f"watermark_removed_{job_id[:8]}.pdf"
    )


# Mount static frontend files
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
