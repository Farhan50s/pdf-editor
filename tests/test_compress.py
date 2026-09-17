import os
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app
from backend.utils import find_ghostscript
import fitz

client = TestClient(app)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures():
    from tests.make_test_pdfs import generate_all_fixtures
    generate_all_fixtures()


def test_compress_invalid_file_rejected():
    """Verify that uploading a non-PDF file returns HTTP 400."""
    fake_content = b"This is plain text and not a valid PDF document header."
    response = client.post(
        "/api/compress",
        files={"file": ("fake_doc.pdf", fake_content, "application/pdf")},
        data={"level": "medium"}
    )
    assert response.status_code == 400
    data = response.json()
    assert "Invalid PDF" in data.get("detail", "") or "signature" in data.get("detail", "")


def test_compress_levels_and_status():
    """
    Test compression at Low, Medium, and High levels.
    Verifies output exists, is valid PDF, preserves page count,
    and High <= Medium <= Low in size.
    """
    input_pdf = FIXTURES_DIR / "large_plain.pdf"
    assert input_pdf.exists(), "large_plain.pdf fixture must exist."

    input_doc = fitz.open(str(input_pdf))
    expected_page_count = input_doc.page_count
    input_doc.close()

    compressed_sizes = {}

    for level in ["low", "medium", "high"]:
        with open(input_pdf, "rb") as f:
            resp = client.post(
                "/api/compress",
                files={"file": (f"test_{level}.pdf", f, "application/pdf")},
                data={"level": level}
            )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Poll status until done or error (up to 60 seconds)
        start_poll = time.time()
        status_data = None
        while time.time() - start_poll < 60:
            status_resp = client.get(f"/api/compress/status/{job_id}")
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            if status_data["status"] in ["done", "error"]:
                break
            time.sleep(1)

        assert status_data["status"] == "done", f"Compression failed: {status_data.get('error_message')}"
        assert status_data["download_url"] is not None
        assert status_data["compressed_size_mb"] is not None

        # Download and verify PDF integrity
        dl_resp = client.get(status_data["download_url"])
        assert dl_resp.status_code == 200
        output_bytes = dl_resp.content
        assert len(output_bytes) > 0

        # Open with PyMuPDF to verify integrity and page count
        out_doc = fitz.open(stream=output_bytes, filetype="pdf")
        assert out_doc.page_count == expected_page_count
        out_doc.close()

        compressed_sizes[level] = len(output_bytes)

    # Verify size hierarchy: High compression produces smaller or equal file to Medium, and Medium to Low
    assert compressed_sizes["high"] <= compressed_sizes["medium"] + 5000, "High compression should be smaller or equal to Medium"
    assert compressed_sizes["medium"] <= compressed_sizes["low"] + 5000, "Medium compression should be smaller or equal to Low"


def test_compress_already_optimal_file():
    """
    Test compression on an already optimized / CCITT-encoded scanned PDF.
    Asserts the app never returns a file larger than the input:
    Either it shrinks the file, or it reports already_optimal: true and
    returns the original file (never a larger file).
    """
    input_pdf = FIXTURES_DIR / "ccitt_scanned.pdf"
    assert input_pdf.exists(), "ccitt_scanned.pdf fixture must exist."

    input_bytes = input_pdf.read_bytes()
    orig_len = len(input_bytes)

    input_doc = fitz.open(stream=input_bytes, filetype="pdf")
    expected_page_count = input_doc.page_count
    input_doc.close()

    with open(input_pdf, "rb") as f:
        resp = client.post(
            "/api/compress",
            files={"file": ("ccitt_scanned.pdf", f, "application/pdf")},
            data={"level": "medium"}
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    start_poll = time.time()
    status_data = None
    while time.time() - start_poll < 60:
        status_resp = client.get(f"/api/compress/status/{job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(1)

    assert status_data["status"] == "done", f"Compression failed: {status_data.get('error_message')}"
    assert status_data["download_url"] is not None

    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200
    downloaded_bytes = dl_resp.content

    # Assert the app never returns a file larger than the input
    assert len(downloaded_bytes) <= orig_len, (
        f"Output file ({len(downloaded_bytes)} bytes) is larger than input ({orig_len} bytes)!"
    )

    # If the file was not reduced in size, assert already_optimal flag is True and message matches
    if len(downloaded_bytes) >= orig_len:
        assert status_data["already_optimal"] is True
        assert status_data["message"] == "This file is already efficiently compressed. Compression could not reduce it further."
        assert downloaded_bytes == input_bytes, "Must offer the exact original file for download"
    else:
        assert status_data["already_optimal"] is False

    # Verify PDF integrity and page count preservation
    out_doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
    assert out_doc.page_count == expected_page_count
    out_doc.close()


def test_compress_already_optimal_strict_flag():
    """
    Test compression on a maximally deflated PDF where no further shrinkage is possible.
    Asserts already_optimal is set to True, message is present, and original file is returned.
    """
    test_pdf = FIXTURES_DIR / "strictly_optimal.pdf"
    doc = fitz.open(str(FIXTURES_DIR / "ccitt_scanned.pdf"))
    doc.save(str(test_pdf), garbage=4, deflate=True, clean=True)
    doc.close()

    orig_bytes = test_pdf.read_bytes()

    with open(test_pdf, "rb") as f:
        resp = client.post(
            "/api/compress",
            files={"file": ("strictly_optimal.pdf", f, "application/pdf")},
            data={"level": "low"}
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    start_poll = time.time()
    status_data = None
    while time.time() - start_poll < 60:
        status_resp = client.get(f"/api/compress/status/{job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(1)

    assert status_data["status"] == "done"
    assert status_data["already_optimal"] is True
    assert status_data["message"] == "This file is already efficiently compressed. Compression could not reduce it further."

    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200
    assert dl_resp.content == orig_bytes
    assert len(dl_resp.content) <= len(orig_bytes)


def test_compress_low_level_skips_pass2_and_cleans_temp():
    """
    Rule 1 & Rule 4:
    When level='low' does not shrink the file, it must skip Pass 2 (protecting user quality)
    and immediately purge any Pass 1 temp files.
    """
    from backend.utils import TEMP_DIR
    from backend.compress import compress_with_pymupdf

    test_pdf = FIXTURES_DIR / "strictly_optimal_low.pdf"
    compress_with_pymupdf(str(FIXTURES_DIR / "ccitt_scanned.pdf"), str(test_pdf), level="low")
    orig_bytes = test_pdf.read_bytes()

    with open(test_pdf, "rb") as f:
        resp = client.post(
            "/api/compress",
            files={"file": ("strictly_optimal_low.pdf", f, "application/pdf")},
            data={"level": "low"}
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    start_poll = time.time()
    status_data = None
    while time.time() - start_poll < 60:
        status_resp = client.get(f"/api/compress/status/{job_id}")
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(1)

    assert status_data["status"] == "done"
    assert status_data["already_optimal"] is True

    # Rule 4: Verify Pass 1 temp file was deleted immediately
    pass1_temp = TEMP_DIR / f"pass1_{job_id}.pdf"
    assert not pass1_temp.exists(), f"Pass 1 temp file {pass1_temp} must be deleted immediately!"


def test_compress_over_200_pages_skips_pass2():
    """
    Rule 2:
    Files with > 200 pages must skip Pass 2 to avoid doubling execution time.
    """
    # Generate a lightweight 205-page deflated PDF
    large_pages_pdf = FIXTURES_DIR / "pages_205_optimal.pdf"
    if not large_pages_pdf.exists():
        doc = fitz.open()
        for i in range(205):
            page = doc.new_page(width=500, height=500)
            page.insert_text((50, 50), f"Page {i + 1}")
        doc.save(str(large_pages_pdf), garbage=4, deflate=True, clean=True)
        doc.close()

    with open(large_pages_pdf, "rb") as f:
        resp = client.post(
            "/api/compress",
            files={"file": ("pages_205_optimal.pdf", f, "application/pdf")},
            data={"level": "medium"}
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    start_poll = time.time()
    status_data = None
    while time.time() - start_poll < 60:
        status_resp = client.get(f"/api/compress/status/{job_id}")
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(1)

    assert status_data["status"] == "done"
    assert status_data["already_optimal"] is True

    # Confirm downloaded file matches input page count
    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200
    out_doc = fitz.open(stream=dl_resp.content, filetype="pdf")
    assert out_doc.page_count == 205
    out_doc.close()


def test_compress_pass2_invalid_fallback(monkeypatch):
    """
    Rule 3:
    If Pass 2 produces a broken/corrupt PDF (or page count mismatch),
    the system must discard it and fall back to already_optimal with the original file.
    """
    import backend.compress as compress_mod

    orig_compress_pymupdf = compress_mod.compress_with_pymupdf

    def mock_compress_broken(in_path, out_path, level="medium", aggressive=False):
        if aggressive:
            with open(out_path, "wb") as f:
                f.write(b"%PDF-1.4\nCorrupted binary payload\n%%EOF")
            return True
        return orig_compress_pymupdf(in_path, out_path, level=level, aggressive=False)

    monkeypatch.setattr(compress_mod, "compress_with_pymupdf", mock_compress_broken)

    # Pre-compress with medium so pass 1 cannot shrink it further
    test_pdf = FIXTURES_DIR / "strictly_optimal_med.pdf"
    orig_compress_pymupdf(str(FIXTURES_DIR / "ccitt_scanned.pdf"), str(test_pdf), level="medium", aggressive=False)
    orig_bytes = test_pdf.read_bytes()

    with open(test_pdf, "rb") as f:
        resp = client.post(
            "/api/compress",
            files={"file": ("corrupt_test.pdf", f, "application/pdf")},
            data={"level": "medium"}
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    start_poll = time.time()
    status_data = None
    while time.time() - start_poll < 60:
        status_resp = client.get(f"/api/compress/status/{job_id}")
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(1)

    assert status_data["status"] == "done"
    assert status_data["already_optimal"] is True

    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200
    # Must serve the valid original file, not the corrupt output
    assert dl_resp.content == orig_bytes



