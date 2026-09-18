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


def make_multi_watermark_pdf(target_path: Path, logo_path: Path, num_pages: int = 10):
    """
    Generates a PDF with unique body text and TWO distinct repeating watermarks on every page:
    1. A repeated diagonal text watermark: 'CONFIDENTIAL DRAFT'
    2. A repeated image logo watermark at top-right
    """
    if target_path.exists() and target_path.stat().st_size > 1000:
        return
    c = canvas.Canvas(str(target_path), pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        # 1. Unique body text
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 72, f"Technical Specification Document — Section {page_num}")
        c.setFont("Helvetica", 11)
        for line_idx in range(14):
            c.drawString(72, height - 100 - (line_idx * 22), f"Proprietary algorithm specification line {line_idx} with seed value {page_num * 50 + line_idx}")

        # 2. Repeated Image Watermark (Logo at top-right)
        c.drawImage(str(logo_path), width - 180, height - 65, width=110, height=45, mask="auto")

        # 3. Repeated Text Watermark (Diagonal rotated text)
        c.saveState()
        c.setFont("Helvetica-Bold", 40)
        c.setFillColor(colors.Color(0.85, 0.15, 0.15, alpha=0.35))
        c.translate(width / 2, height / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, "CONFIDENTIAL DRAFT")
        c.restoreState()

        c.setFont("Helvetica", 10)
        c.setFillColor(colors.black)
        c.drawString(72, 40, f"Page {page_num} of {num_pages}")
        c.showPage()

    c.save()


def make_large_plain_pdf(target_path: Path, num_pages: int = 500):
    """
    Generates a ~40-60MB plain PDF of 500 pages with varied images for compression tests.
    Caches generation to ensure fast test suites.
    """
    if target_path.exists() and target_path.stat().st_size > 10 * 1024 * 1024:
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


def make_ccitt_scanned_pdf(target_path: Path, num_pages: int = 3):
    """
    Generates a low-size scanned-style PDF with 1-bit monochrome CCITT Group 4 images.
    This simulates an already efficiently compressed / CCITT-encoded scanned PDF.
    """
    if target_path.exists() and target_path.stat().st_size > 500:
        return

    import fitz

    doc = fitz.open()
    for page_idx in range(num_pages):
        img = PILImage.new("1", (600, 800), 1)
        draw = PILImageDraw.Draw(img)
        for y in range(80, 750, 25):
            draw.line([(50, y), (550, y)], fill=0, width=1)
        draw.text((60, 40), f"SCANNED ARCHIVE RECORD - PAGE {page_idx + 1}", fill=0)

        buf = io.BytesIO()
        img.save(buf, format="TIFF", compression="group4")
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.getvalue())

    doc.save(str(target_path), garbage=4, deflate=True, clean=True)
    doc.close()
    print(f"[Fixtures] Generated ccitt_scanned.pdf with size: {target_path.stat().st_size} bytes")


def make_halftone_scanned_pdf(target_path: Path, num_pages: int = 5):
    """
    Generates a synthetic halftone-style scanned PDF fixture (dithered pattern with visible text,
    similar to a real scanned textbook).
    """
    if target_path.exists() and target_path.stat().st_size > 500:
        return

    import fitz

    doc = fitz.open()
    for p in range(num_pages):
        img = PILImage.new("L", (850, 1100), 255)
        draw = PILImageDraw.Draw(img)
        draw.text((70, 70), f"CHAPTER {p + 1}: PROBABILITY THEORY AND NORMAL DISTRIBUTION", fill=0)
        for y in range(120, 960, 26):
            draw.text((70, y), f"Section {y // 26}: The variance of random variable X is Var(X) = E[(X - mu)^2]. Equation [{p + 1}.{y // 26}]", fill=0)
        draw.line([(70, 980), (780, 980)], fill=0, width=2)
        draw.text((400, 1010), f"Page {p + 1} of {num_pages}", fill=0)

        # Dither to halftone Floyd-Steinberg pattern
        dithered = img.convert("1", dither=PILImage.Dither.FLOYDSTEINBERG)
        buf = io.BytesIO()
        dithered.save(buf, format="PNG")

        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=buf.getvalue())

    doc.save(str(target_path), garbage=4, deflate=True, clean=True)
    doc.close()
    print(f"[Fixtures] Generated halftone_scanned.pdf with size: {target_path.stat().st_size} bytes")


def make_yellowed_scanned_watermark_pdf(target_path: Path, num_pages: int = 5):
    """
    Generates a synthetic scanned book PDF with:
    - Yellowed/off-white background (~180 luminance, e.g. RGB(185, 180, 168)).
    - Book body content (statistical equations and typography).
    - Repeating overlay watermark stamps in header and footer (top 12% and bottom 10%).
    """
    if target_path.exists() and target_path.stat().st_size > 500:
        return

    import fitz

    doc = fitz.open()
    for p in range(num_pages):
        img = PILImage.new("RGB", (850, 1100), (185, 180, 168))
        draw = PILImageDraw.Draw(img)

        # 1. Header watermark (in the top 12% exclusion band: y < 132)
        draw.text((120, 45), "--- PROPERTY OF ARCHIVE LIBRARY - WATERMARK DO NOT REMOVE ---", fill=(80, 75, 70))
        draw.line([(50, 85), (800, 85)], fill=(120, 115, 105), width=2)

        # 2. Main body content (central 78% of height: y between 132 and 990)
        draw.text((70, 150), f"CHAPTER {p + 1}: STATISTICAL INFERENCE & HYPOTHESIS TESTING", fill=(30, 25, 20))
        for y in range(200, 930, 28):
            draw.text(
                (70, y),
                f"Equation [{p + 1}.{y // 28}]: Let X_1, ..., X_n be i.i.d. with density f(x; theta). The likelihood function L(theta) = prod f(X_i; theta).",
                fill=(35, 30, 25)
            )

        # 3. Footer watermark (in the bottom 10% exclusion band: y > 990)
        draw.line([(50, 1000), (800, 1000)], fill=(120, 115, 105), width=2)
        draw.text((180, 1025), "DIGITIZED WATERMARK STAMP - FREE SCAN - WWW.ACADEMICARCHIVE.ORG", fill=(75, 70, 65))
        draw.text((410, 1055), f"Page {p + 1} of {num_pages}", fill=(40, 35, 30))

        buf = io.BytesIO()
        img.save(buf, format="PNG")

        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=buf.getvalue())

    doc.save(str(target_path), garbage=4, deflate=True, clean=True)
    doc.close()
    print(f"[Fixtures] Generated yellowed_scanned_watermark.pdf with size: {target_path.stat().st_size} bytes")


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

    print("[Fixtures] Generating ccitt_scanned.pdf...")
    make_ccitt_scanned_pdf(FIXTURES_DIR / "ccitt_scanned.pdf", num_pages=3)

    print("[Fixtures] Generating halftone_scanned.pdf...")
    make_halftone_scanned_pdf(FIXTURES_DIR / "halftone_scanned.pdf", num_pages=5)

    print("[Fixtures] Generating yellowed_scanned_watermark.pdf...")
    make_yellowed_scanned_watermark_pdf(FIXTURES_DIR / "yellowed_scanned_watermark.pdf", num_pages=5)

    print("[Fixtures] Generating multi_watermark.pdf...")
    make_multi_watermark_pdf(FIXTURES_DIR / "multi_watermark.pdf", logo_path, num_pages=10)

    print("[Fixtures] All test fixtures ready in tests/fixtures/.")


if __name__ == "__main__":
    generate_all_fixtures()
