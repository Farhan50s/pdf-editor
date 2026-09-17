# PDF Tool — Local Compressor & Watermark Remover

A high-performance local web utility for personal use that runs entirely on `localhost`. No login, no cloud uploads, and zero third-party dependencies.

## Key Features

1. **PDF Compression (Ghostscript engine)**
   - **Low Compression**: High quality, prepress settings (`-dPDFSETTINGS=/prepress`).
   - **Medium Compression**: Balanced size/quality, eBook settings (`-dPDFSETTINGS=/ebook`).
   - **High Compression**: Maximum size reduction, screen settings (`-dPDFSETTINGS=/screen`).
   - Handles documents up to 500+ pages and 50MB+ without freezing.

2. **PDF Watermark Removal (PyMuPDF engine)**
   - **Surgical Span-Level Redaction**: Redacts exact text span rectangles rather than broad line bounding boxes, preventing accidental erasure of page body content, charts, or formulas on diagonal watermarks.
   - **Graphics & Image Preservation**: Calls `page.apply_redactions(images=PDF_REDACT_IMAGE_NONE, graphics=PDF_REDACT_LINE_ART_NONE)` so underlying images, line art, and diagrams are 100% preserved.
   - **Multi-Type Watermark Detection**: Supports repeated text watermarks, image XObjects (using xref and MD5 byte hashes), and vector graphic line art.
   - **Smart Sampling**: Samples evenly across the beginning, middle, and end of the document (skipping cover/TOC bias).
   - **Visual Confirmation**: Renders Page 1 preview thumbnail with a red bounding box around the candidate.
   - **Post-Removal Verification**: Validates 20-30 pages (including page 0 and the final page) to ensure the watermark is completely purged before delivering the download.

---

## Installation & Setup

### 1. Requirements

- Python 3.10+ (Tested on Python 3.13)
- Ghostscript (external binary)

### 2. Install Ghostscript

- **Windows**:
  - Download the official AGPL 64-bit installer from [ghostscript.com/releases](https://ghostscript.com/releases/gsdnld.html).
  - Run installer and add Ghostscript's `bin` folder (e.g. `C:\Program Files\gs\gs10.xx.x\bin`) to your system `PATH`.
  - Verify install in terminal: `gswin64c -v` or `gs --version`.
- **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt install ghostscript
  ```
- **macOS**:
  ```bash
  brew install ghostscript
  ```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## Running the Application

Start the local server using Uvicorn:

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open your browser and navigate to:
```
http://localhost:8000
```

---

## Running the Automated Test Suite

Generate fixtures (if not already cached) and run the full pytest test suite:

```bash
python -m pytest tests/ -v
```

All test PDFs are cached in `tests/fixtures/` for fast subsequent test execution.
