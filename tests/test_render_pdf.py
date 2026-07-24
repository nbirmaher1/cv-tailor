from render_pdf import render

SHORT_HTML = "<!doctype html><html><body><h1>Jane Doe</h1><p>A short CV.</p></body></html>"

LONG_HTML = (
    "<!doctype html><html><body>"
    + "<p>Filler paragraph to push content onto a second printed page.</p>" * 200
    + "</body></html>"
)


def test_render_short_html_produces_single_page(tmp_path):
    html_path = tmp_path / "short.html"
    html_path.write_text(SHORT_HTML)
    pdf_path = tmp_path / "short.pdf"

    page_count = render(str(html_path), str(pdf_path))

    assert pdf_path.exists()
    assert page_count == 1


def test_render_long_html_spills_to_multiple_pages(tmp_path):
    html_path = tmp_path / "long.html"
    html_path.write_text(LONG_HTML)
    pdf_path = tmp_path / "long.pdf"

    page_count = render(str(html_path), str(pdf_path))

    assert page_count > 1
