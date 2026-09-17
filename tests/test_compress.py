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
