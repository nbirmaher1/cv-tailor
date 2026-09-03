from fill_html import CSS_TWEAKS, apply_css_tweaks, fill_cover_letter_html, fill_cv_html


def _minimal_cv():
    return {
        "full_name": "Jane Doe",
        "target_title": "Data Analyst",
        "email": "jane@example.com",
        "phone": "",
        "location": "Berlin, Germany",
        "links": "",
        "work_authorization": "",
        "photo_path": None,
        "summary": "Analyst with 5 years of experience.",
        "experience": [
            {"title": "Analyst", "company": "Acme", "location": "Remote", "dates": "2020-Present",
             "bullets": ["Built a <reporting> pipeline & cut runtime by 30%"]},
        ],
        "education": [{"degree": "BSc CS", "school": "TU Berlin", "location": "Berlin", "dates": "2016-2020"}],
        "skills": [{"category": "Tools", "items": ["SQL", "Python"]}],
        "languages": ["English", "German"],
        "extra_sections": [{"heading": "Certifications", "items": ["AWS Certified"]}],
    }


def test_fill_cv_html_includes_core_fields():
    html = fill_cv_html(_minimal_cv())
    assert "Jane Doe" in html
    assert "Data Analyst" in html
    assert "Berlin, Germany" in html
    assert "Analyst" in html and "Acme" in html
    assert "BSc CS" in html
    assert "SQL, Python" in html
    assert "English" in html and "German" in html
    assert "AWS Certified" in html


def test_fill_cv_html_escapes_bullet_content():
    html = fill_cv_html(_minimal_cv())
    assert "<reporting>" not in html
    assert "&lt;reporting&gt;" in html
    assert "&amp;" in html


def test_fill_cv_html_drops_photo_wrapper_when_no_photo():
    html = fill_cv_html(_minimal_cv())
    assert 'class="header"' not in html
    assert "<img" not in html


def test_fill_cv_html_includes_photo_when_present():
    content = _minimal_cv()
    content["photo_path"] = "/tmp/photo.jpg"
    html = fill_cv_html(content)
    assert 'class="header"' in html
    assert 'src="/tmp/photo.jpg"' in html


def test_fill_cv_html_drops_empty_contact_fields():
    html = fill_cv_html(_minimal_cv())
    # phone/links/work_authorization are all "" in the fixture -- no stray empty spans.
    assert "<span></span>" not in html
    contact_start = html.index('<div class="contact">')
    contact_end = html.index("</div>", contact_start)
    contact_html = html[contact_start:contact_end]
    assert contact_html.count("<span>") == 2


def test_fill_cv_html_handles_missing_optional_sections():
    minimal = {"full_name": "Jane Doe"}
    html = fill_cv_html(minimal)
    assert "Jane Doe" in html
    assert "Experience" not in html
    assert "Education" not in html
    assert "Skills" not in html
    assert "Languages" not in html


def test_fill_cv_html_multiple_skill_groups_without_category():
    content = _minimal_cv()
    content["skills"] = [{"category": None, "items": ["SQL", "Python"]}]
    html = fill_cv_html(content)
    assert 'class="skill-cat"' not in html
    assert "SQL, Python" in html


def test_fill_cover_letter_html_includes_paragraphs_and_signature():
    content = {
        "full_name": "Jane Doe", "email": "jane@example.com", "phone": "555-1234",
        "location": "Berlin, Germany", "date": "March 3, 2026", "salutation": "Dear Hiring Manager,",
        "paragraphs": ["First paragraph.", "Second paragraph."],
        "closing": "Sincerely,", "signature_name": "Jane Doe",
    }
    html = fill_cover_letter_html(content)
    assert "Dear Hiring Manager," in html
    assert "First paragraph." in html and "Second paragraph." in html
    assert "Sincerely," in html
    assert html.count('class="body-para"') == 2


def test_apply_css_tweaks_applies_only_requested_count():
    html = "<style>padding: 26px 48px; line-height: 1.15; font-size: 10.5pt;</style>"
    tweaked = apply_css_tweaks(html, 1)
    assert "padding: 22px 44px;" in tweaked
    assert "line-height: 1.10;" not in tweaked


def test_apply_css_tweaks_is_cumulative():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "default.html").read_text()
    tweaked = apply_css_tweaks(html, len(CSS_TWEAKS))
    for _, tweak_to in CSS_TWEAKS:
        assert tweak_to in tweaked


def test_css_tweaks_are_unique_substrings_of_default_template():
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "templates" / "default.html").read_text()
    for tweak_from, _ in CSS_TWEAKS:
        assert template.count(tweak_from) == 1, f"{tweak_from!r} must appear exactly once in default.html"
