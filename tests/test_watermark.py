import os
import time
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app
import fitz

client = TestClient(app)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures():
    from tests.make_test_pdfs import generate_all_fixtures
    generate_all_fixtures()


def test_watermark_detect_no_watermark():
    """Verify that a document without recurring watermarks yields empty candidates."""
    input_pdf = FIXTURES_DIR / "no_watermark.pdf"
    assert input_pdf.exists()

    with open(input_pdf, "rb") as f:
        resp = client.post("/api/watermark/detect", files={"file": ("no_watermark.pdf", f, "application/pdf")})

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] is not None
    assert len(data["candidates"]) == 0, f"Expected 0 candidates, got: {data['candidates']}"


def test_watermark_detect_and_remove_text():
    """
    Verify detection and surgical removal of repeating text watermark 'CONFIDENTIAL'.
    Ensures watermark is eliminated while body text is completely preserved.
    """
    input_pdf = FIXTURES_DIR / "text_watermark_small.pdf"
    assert input_pdf.exists()

    # 1. Detection
    with open(input_pdf, "rb") as f:
        detect_resp = client.post("/api/watermark/detect", files={"file": ("text_watermark_small.pdf", f, "application/pdf")})

    assert detect_resp.status_code == 200
    detect_data = detect_resp.json()
    job_id = detect_data["job_id"]
    candidates = detect_data["candidates"]
    assert len(candidates) >= 1, "Should find at least 1 watermark candidate"

    # Find the text candidate containing CONFIDENTIAL
    confidential_cand = next((c for c in candidates if c["type"] == "text" and "CONFIDENTIAL" in c.get("sample_text", "")), None)
    assert confidential_cand is not None, f"CONFIDENTIAL text candidate not found in {candidates}"
    assert confidential_cand["pages_found_on"] >= 8

    # 2. Preview check
    preview_url = confidential_cand["page_preview_image_url"]
    preview_resp = client.get(preview_url)
    assert preview_resp.status_code == 200
    assert preview_resp.headers["content-type"] == "image/png"

    # 3. Removal
    remove_resp = client.post("/api/watermark/remove", json={
        "job_id": job_id,
        "candidate_id": confidential_cand["candidate_id"]
    })
    assert remove_resp.status_code == 200
    remove_job_id = remove_resp.json()["job_id"]

    # Poll status
    start_time = time.time()
    status_data = None
    while time.time() - start_time < 30:
        status_resp = client.get(f"/api/watermark/status/{remove_job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(0.5)

    assert status_data["status"] == "done", f"Removal failed: {status_data.get('error_message')}"
    assert status_data["download_url"] is not None

    # 4. Download and verify integrity
    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200

    doc = fitz.open(stream=dl_resp.content, filetype="pdf")
    assert doc.page_count == 10

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_text = page.get_text()
        # Assert watermark is completely removed
        assert "CONFIDENTIAL" not in page_text, f"Watermark found on page {page_idx + 1}"
        # Assert body content is preserved
        assert "Chapter" in page_text or "Critical project metrics" in page_text, f"Body text missing on page {page_idx + 1}"
    doc.close()


def test_watermark_detect_and_remove_image():
    """Verify detection and deletion of repeated image watermark."""
    input_pdf = FIXTURES_DIR / "image_watermark.pdf"
    assert input_pdf.exists()

    # 1. Detection
    with open(input_pdf, "rb") as f:
        detect_resp = client.post("/api/watermark/detect", files={"file": ("image_watermark.pdf", f, "application/pdf")})

    assert detect_resp.status_code == 200
    detect_data = detect_resp.json()
    job_id = detect_data["job_id"]
    candidates = detect_data["candidates"]
    assert len(candidates) >= 1, "Should find at least 1 candidate"

    img_cand = next((c for c in candidates if c["type"] == "image"), None)
    assert img_cand is not None, f"Image candidate not found in {candidates}"
    assert img_cand["pages_found_on"] >= 8

    # 2. Removal
    remove_resp = client.post("/api/watermark/remove", json={
        "job_id": job_id,
        "candidate_id": img_cand["candidate_id"]
    })
    assert remove_resp.status_code == 200
    remove_job_id = remove_resp.json()["job_id"]

    start_time = time.time()
    status_data = None
    while time.time() - start_time < 30:
        status_resp = client.get(f"/api/watermark/status/{remove_job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(0.5)

    assert status_data["status"] == "done", f"Removal failed: {status_data.get('error_message')}"

    # 3. Verify cleaned PDF
    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200

    doc = fitz.open(stream=dl_resp.content, filetype="pdf")
    assert doc.page_count == 10
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        images = page.get_images(full=True)
        assert len(images) == 0, f"Watermark image still present on page {page_idx + 1}"
    doc.close()


def test_watermark_large_file_performance():
    """
    Tests detect + remove on a 500-page document (text_watermark_large.pdf).
    Verifies performance completes well under 5 minutes without memory crash.
    """
    input_pdf = FIXTURES_DIR / "text_watermark_large.pdf"
    assert input_pdf.exists()

    start_total = time.time()

    # 1. Detection
    with open(input_pdf, "rb") as f:
        detect_resp = client.post("/api/watermark/detect", files={"file": ("text_watermark_large.pdf", f, "application/pdf")})

    assert detect_resp.status_code == 200
    detect_data = detect_resp.json()
    job_id = detect_data["job_id"]
    assert detect_data["page_count"] == 500

    cand = next((c for c in detect_data["candidates"] if "CONFIDENTIAL" in c.get("sample_text", "")), None)
    assert cand is not None, "Candidate CONFIDENTIAL not found on 500-page document"

    # 2. Removal
    remove_resp = client.post("/api/watermark/remove", json={
        "job_id": job_id,
        "candidate_id": cand["candidate_id"]
    })
    assert remove_resp.status_code == 200
    remove_job_id = remove_resp.json()["job_id"]

    # Poll status (allow up to 5 minutes = 300 seconds)
    status_data = None
    while time.time() - start_total < 300:
        status_resp = client.get(f"/api/watermark/status/{remove_job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        if status_data["status"] in ["done", "error"]:
            break
        time.sleep(2)

    total_duration = time.time() - start_total
    print(f"\n[Performance] 500-page watermark removal completed in {total_duration:.2f}s")
    assert status_data["status"] == "done", f"Removal failed: {status_data.get('error_message')}"
    assert status_data["progress"] == 100
    assert total_duration < 300, f"Processing exceeded 5 minutes: {total_duration}s"

    # 3. Sample-check 25 pages including first and last
    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200

    doc = fitz.open(stream=dl_resp.content, filetype="pdf")
    assert doc.page_count == 500
    check_pages = [0, 499] + [i * 20 for i in range(1, 25)]
    for p_no in check_pages:
        page_text = doc[p_no].get_text()
        assert "CONFIDENTIAL" not in page_text, f"Watermark still found on page {p_no + 1}"
        assert "Chapter" in page_text, f"Body text damaged on page {p_no + 1}"
    doc.close()


def test_watermark_password_protected_pdf():
    """Verify that password-protected PDF returns clean HTTP 400 error without crash."""
    input_pdf = FIXTURES_DIR / "password_protected.pdf"
    assert input_pdf.exists()

    with open(input_pdf, "rb") as f:
        resp = client.post("/api/watermark/detect", files={"file": ("password_protected.pdf", f, "application/pdf")})

    assert resp.status_code == 400
    data = resp.json()
    assert "password" in data.get("detail", "").lower()


def test_watermark_multi_select_single_pass_removal(monkeypatch):
    """
    Verify multi-select watermark removal in a single page pass:
    1. Detect multiple distinct watermarks (text 'CONFIDENTIAL DRAFT' and image logo).
    2. Pass candidate_ids containing both to POST /api/watermark/remove.
    3. Assert that both watermarks are completely eliminated in the output PDF.
    4. Assert that the body content is 100% preserved.
    5. Assert that the removal loop iterates the document's pages exactly once (10 accesses, NOT 20).
    """
    input_pdf = FIXTURES_DIR / "multi_watermark.pdf"
    assert input_pdf.exists()

    # 1. Detection
    with open(input_pdf, "rb") as f:
        detect_resp = client.post("/api/watermark/detect", files={"file": ("multi_watermark.pdf", f, "application/pdf")})
    assert detect_resp.status_code == 200
    detect_data = detect_resp.json()
    job_id = detect_data["job_id"]
    candidates = detect_data["candidates"]
    assert len(candidates) >= 2, f"Expected at least 2 watermark candidates, got {len(candidates)}"

    cand_ids = [c["candidate_id"] for c in candidates[:2]]

    # 2. Instrument page access during removal loop to verify single page pass
    page_access_counter = 0
    import backend.watermark as wm_mod
    orig_open = wm_mod.fitz.open

    class SpyingDoc:
        def __init__(self, target):
            self._doc = orig_open(target)
        def __getattr__(self, name):
            return getattr(self._doc, name)
        def __getitem__(self, idx):
            nonlocal page_access_counter
            page_access_counter += 1
            return self._doc[idx]
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self._doc.close()

    def instrumented_open(*args, **kwargs):
        target = args[0] if len(args) > 0 else kwargs.get("filename")
        if isinstance(target, str) and "cleaned_" not in target and target.endswith(".pdf"):
            return SpyingDoc(target)
        return orig_open(*args, **kwargs)

    monkeypatch.setattr(wm_mod.fitz, "open", instrumented_open)

    # 3. Multi-removal request
    remove_resp = client.post("/api/watermark/remove", json={
        "job_id": job_id,
        "candidate_ids": cand_ids
    })
    assert remove_resp.status_code == 200
    remove_job_id = remove_resp.json()["job_id"]

    # Poll status
    status_resp = client.get(f"/api/watermark/status/{remove_job_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "done", f"Removal failed: {status_data.get('error_message')}"
    assert status_data["download_url"] is not None

    # Assert status includes candidates_removed
    assert "candidates_removed" in status_data
    assert set(status_data["candidates_removed"]) == set(cand_ids)

    # Assert single-pass execution: exactly 10 pages visited once during removal (NOT 20 for 2 candidates)
    assert page_access_counter == 10, f"Expected exactly 10 page iterations (single pass), got {page_access_counter}"

    # 4. Download and verify integrity
    dl_resp = client.get(status_data["download_url"])
    assert dl_resp.status_code == 200

    doc = fitz.open(stream=dl_resp.content, filetype="pdf")
    assert doc.page_count == 10

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        text = page.get_text()
        images = page.get_images(full=True)

        # Both watermarks must be gone:
        assert "CONFIDENTIAL DRAFT" not in text, f"Text watermark survived on page {page_idx + 1}"
        assert len(images) == 0, f"Image watermark survived on page {page_idx + 1}"

        # Body text must be preserved:
        assert "Technical Specification Document" in text, f"Body header missing on page {page_idx + 1}"
        assert "Proprietary algorithm specification" in text, f"Body text missing on page {page_idx + 1}"

    doc.close()


def test_watermark_possibly_same_watermark_detection(tmp_path):
    """
    Verify that fragmented text runs on the same line (same y-row, adjacent x)
    are flagged with 'possibly_same_watermark: True'.
    """
    test_pdf_path = tmp_path / "fragmented_test.pdf"
    doc = fitz.open()
    for p in range(5):
        page = doc.new_page(width=600, height=600)
        # Two text spans on the same row (y=60), adjacent horizontally:
        page.insert_text(fitz.Point(100, 60), "WATERMARK_AAA", fontsize=14)
        page.insert_text(fitz.Point(240, 60), "WATERMARK_BBB", fontsize=14)
        page.insert_text(fitz.Point(100, 250), f"Body paragraph line on page {p + 1}", fontsize=12)
    doc.save(str(test_pdf_path))
    doc.close()

    with open(test_pdf_path, "rb") as f:
        resp = client.post("/api/watermark/detect", files={"file": ("fragmented.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    candidates = data["candidates"]

    # Both candidates should be found and flagged as possibly_same_watermark: True
    c_aaa = next((c for c in candidates if "WATERMARK_AAA" in c.get("sample_text", "")), None)
    c_bbb = next((c for c in candidates if "WATERMARK_BBB" in c.get("sample_text", "")), None)
    assert c_aaa is not None, f"WATERMARK_AAA not detected in {candidates}"
    assert c_bbb is not None, f"WATERMARK_BBB not detected in {candidates}"

    assert c_aaa.get("possibly_same_watermark") is True
    assert c_bbb.get("possibly_same_watermark") is True
