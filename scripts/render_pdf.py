#!/usr/bin/env python3
"""Render an HTML file to a PDF using a headless Chromium instance (Playwright)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from pypdf import PdfReader


def render(html_path, pdf_path):
    html_path = Path(html_path).resolve()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()
    return len(PdfReader(str(pdf_path)).pages)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: render_pdf.py <input.html> <output.pdf>", file=sys.stderr)
        sys.exit(1)
    page_count = render(sys.argv[1], sys.argv[2])
    print(f"Wrote {sys.argv[2]} ({page_count} page{'s' if page_count != 1 else ''})")
