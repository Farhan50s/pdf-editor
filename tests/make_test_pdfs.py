import os
import io
import random
from pathlib import Path
from PIL import Image as PILImage, ImageDraw as PILImageDraw

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def create_sample_logo(logo_path: Path):
    """Creates a sample logo PNG with a distinct colored icon and text."""
    if logo_path.exists():
        return
    img = PILImage.new("RGBA", (120, 50), (255, 255, 255, 0))
    draw = PILImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 118, 48], radius=8, fill=(37, 99, 235, 180), outline=(29, 78, 216, 255), width=2)
    draw.text((15, 16), "CORP LOGO", fill=(255, 255, 255, 255))
    img.save(str(logo_path), format="PNG")


def create_large_filler_image(img_path: Path):
    """Creates an ~80-100KB JPEG image for large PDF testing."""
    if img_path.exists():
        return
    # 600x600 image with random noise patterns to ensure compression has real data to compress
    img = PILImage.new("RGB", (600, 600), (240, 240, 240))
    draw = PILImageDraw.Draw(img)
    for i in range(0, 600, 20):
        draw.line([(0, i), (600, 600 - i)], fill=(i % 255, (i * 2) % 255, (i * 3) % 255), width=2)
    img.save(str(img_path), format="JPEG", quality=85)


def make_no_watermark_pdf(target_path: Path, num_pages: int = 10):
    """Generates a PDF with unique text on every page and no repeating watermark."""
    if target_path.exists() and target_path.stat().st_size > 1000:
        return
    c = canvas.Canvas(str(target_path), pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, height - 72, f"Section {page_num}: Unique Document Content")
        c.setFont("Helvetica", 11)
        
        # Write unique body text
        for line_idx in range(15):
            unique_word = f"data_token_{page_num}_{line_idx}_{random.randint(1000, 9999)}"
            c.drawString(72, height - 110 - (line_idx * 20), f"Body paragraph line {line_idx + 1} with variable content: {unique_word}")

        c.drawString(72, 50, f"Page {page_num} of {num_pages}")
        c.showPage()

    c.save()


def make_text_watermark_pdf(target_path: Path, num_pages: int = 10, watermark_text: str = "CONFIDENTIAL"):
    """Generates a PDF with unique body text and a repeated watermark text on every page."""
    if target_path.exists() and target_path.stat().st_size > 1000:
        return
    c = canvas.Canvas(str(target_path), pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        # 1. Unique body text
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 72, f"Chapter {page_num} — Confidential Report")
        c.setFont("Helvetica", 11)
        for line_idx in range(18):
            token = f"val_{page_num}_{line_idx}_{random.randint(100, 999)}"
            c.drawString(72, height - 100 - (line_idx * 22), f"Critical project metrics and calculations: token={token}")

        # 2. Repeated Watermark Text (Diagonal / Rotated)
        c.saveState()
        c.setFont("Helvetica-Bold", 42)
        c.setFillColor(colors.Color(0.8, 0.2, 0.2, alpha=0.35))
        c.translate(width / 2, height / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, watermark_text)
        c.restoreState()

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.black)
        c.drawString(72, 40, f"Document Index: {page_num}")
        c.showPage()

    c.save()


def make_image_watermark_pdf(target_path: Path, logo_path: Path, num_pages: int = 10):
    """Generates a PDF with unique body text and a repeated image logo on every page."""
    if target_path.exists() and target_path.stat().st_size > 1000:
        return
    c = canvas.Canvas(str(target_path), pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 72, f"Quarterly Review - Page {page_num}")
        c.setFont("Helvetica", 11)
        for line_idx in range(12):
            c.drawString(72, height - 100 - (line_idx * 24), f"Financial ledger statement line {line_idx} with ledger key {page_num * 100 + line_idx}")

        # Draw the repeated logo image at top-right
        c.drawImage(str(logo_path), width - 180, height - 70, width=110, height=45, mask="auto")

        c.drawString(72, 40, f"Page {page_num}")
        c.showPage()

    c.save()


def make_large_plain_pdf(target_path: Path, num_pages: int = 500):
    """
    Generates a ~40-60MB plain PDF of 500 pages with varied images for compression tests.
    Caches generation to ensure fast test suites.
    """
    if target_path.exists() and target_path.stat().st_size > 35 * 1024 * 1024:
        return  # Cached!

    print("Generating 45MB large_plain.pdf with distinct embedded images...")
    # Generate 40 distinct 1MB image streams to reach ~45MB total
    distinct_images = []
    for idx in range(40):
        img_file = FIXTURES_DIR / f"distinct_img_{idx}.jpg"
        if not img_file.exists() or img_file.stat().st_size < 500 * 1024:
            # 1000x1000 noisy image to produce ~1.1MB compressed jpeg
            img = PILImage.new("RGB", (900, 900), (idx * 5 % 255, (idx * 15) % 255, (idx * 25) % 255))
            draw = PILImageDraw.Draw(img)
            for j in range(0, 900, 15):
                draw.line([(0, j), (900, 900 - j)], fill=((j + idx * 10) % 255, (j * 2) % 255, (j * 3) % 255), width=3)
            img.save(str(img_file), format="JPEG", quality=92)
        distinct_images.append(img_file)

    c = canvas.Canvas(str(target_path), pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 50, f"High-Resolution Archive Page {page_num} of {num_pages}")
        
        # Cycle through distinct images so ~40-50MB total is embedded across 500 pages
        img_to_use = distinct_images[page_num % len(distinct_images)]
        c.drawImage(str(img_to_use), 72, 80, width=460, height=460, mask=None)
        c.showPage()

    c.save()
    print(f"Generated large_plain.pdf with size: {target_path.stat().st_size / (1024 * 1024):.2f} MB")


def make_password_protected_pdf(target_path: Path, password: str = "secret123"):
    """Generates an encrypted PDF requiring a password to open."""
    if target_path.exists() and target_path.stat().st_size > 500:
        return
    c = canvas.Canvas(str(target_path), pagesize=letter, encrypt=password)
    width, height = letter
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, height - 150, "Restricted Confidential Document")
    c.setFont("Helvetica", 12)
    c.drawString(100, height - 200, "This page is protected with a standard password.")
    c.showPage()
    c.save()


def generate_all_fixtures():
    """Generates all test PDFs into tests/fixtures/ with caching."""
    logo_path = FIXTURES_DIR / "sample_logo.png"
    filler_img_path = FIXTURES_DIR / "filler_img.jpg"
    
    create_sample_logo(logo_path)
    create_large_filler_image(filler_img_path)

    print("[Fixtures] Generating no_watermark.pdf...")
    make_no_watermark_pdf(FIXTURES_DIR / "no_watermark.pdf", num_pages=10)

    print("[Fixtures] Generating text_watermark_small.pdf...")
    make_text_watermark_pdf(FIXTURES_DIR / "text_watermark_small.pdf", num_pages=10, watermark_text="CONFIDENTIAL")

    print("[Fixtures] Generating text_watermark_large.pdf (500 pages)...")
    make_text_watermark_pdf(FIXTURES_DIR / "text_watermark_large.pdf", num_pages=500, watermark_text="CONFIDENTIAL")

    print("[Fixtures] Generating image_watermark.pdf...")
    make_image_watermark_pdf(FIXTURES_DIR / "image_watermark.pdf", logo_path, num_pages=10)

    print("[Fixtures] Generating large_plain.pdf (~40-60MB, 500 pages)...")
    make_large_plain_pdf(FIXTURES_DIR / "large_plain.pdf", num_pages=500)

    print("[Fixtures] Generating password_protected.pdf...")
    make_password_protected_pdf(FIXTURES_DIR / "password_protected.pdf", password="secret123")

    print("[Fixtures] All test fixtures ready in tests/fixtures/.")


if __name__ == "__main__":
    generate_all_fixtures()
