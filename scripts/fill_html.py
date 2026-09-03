#!/usr/bin/env python3
"""Fill templates/default.html and templates/cover_letter.html from a canonical content
JSON dict, deterministically -- the PDF-path equivalent of render_docx.py, so the LLM
never hand-authors HTML output tokens. Uses .get(...) with defaults throughout (unlike
render_docx.py's data["full_name"] direct access) since this sits in the hot render/QA-
retry path and must tolerate a partial or malformed record without raising.

Each template's <head> (including its <style> block) is read back verbatim from the
.html file, so the CSS stays single-sourced there -- this module only ever generates the
<body>.
"""
import html
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Ordered CSS micro-tweaks for near-miss PDF overflow (exactly one page over budget) --
# applied cumulatively by apply_css_tweaks, tightest-last. Each `from` string is a
# literal, unique substring of default.html's <style> block (verified against the file
# as of writing); if that CSS ever changes, these need to change with it. Tweaks 1-2
# (padding, line-height) are imperceptible; 3-5 squeeze harder and are visually
# noticeable, so app.py treats them differently (see _run_render_qa_loop).
CSS_TWEAKS = [
    ("padding: 26px 48px;", "padding: 22px 44px;"),
    ("line-height: 1.15;", "line-height: 1.10;"),
    ("margin: 18px 0 5px 0;", "margin: 14px 0 4px 0;"),  # h2
    ("margin-bottom: 2px;", "margin-bottom: 1px;"),  # li
    ("font-size: 10.5pt;", "font-size: 10pt;"),
]


def apply_css_tweaks(html_str: str, count: int) -> str:
    """Applies the first `count` entries of CSS_TWEAKS cumulatively to an HTML string
    already produced by fill_cv_html."""
    for tweak_from, tweak_to in CSS_TWEAKS[:count]:
        html_str = html_str.replace(tweak_from, tweak_to, 1)
    return html_str


def _load_head(template_name: str) -> str:
    text = (TEMPLATES_DIR / template_name).read_text()
    head, _, _ = text.partition("<body>")
    return head


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _contact_spans(*values) -> str:
    return "".join(f"<span>{_esc(v)}</span>" for v in values if str(v or "").strip())


def fill_cv_html(content: dict) -> str:
    head = _load_head("default.html")

    photo_path = content.get("photo_path")
    contact = _contact_spans(
        content.get("email"), content.get("phone"), content.get("location"),
        content.get("links"), content.get("work_authorization"),
    )
    header_text = (
        f'<h1>{_esc(content.get("full_name"))}</h1>\n'
        f'      <div class="title">{_esc(content.get("target_title"))}</div>\n'
        f'      <div class="contact">{contact}</div>'
    )
    if photo_path:
        body_top = (
            f'<div class="header">\n'
            f'    <div class="header-text">\n'
            f'      {header_text}\n'
            f'    </div>\n'
            f'    <img class="photo" src="{_esc(photo_path)}" alt="">\n'
            f'  </div>'
        )
    else:
        body_top = f'<div class="header-text">\n    {header_text}\n  </div>'

    sections = []

    if content.get("summary"):
        sections.append(
            f'<section>\n    <h2>Summary</h2>\n    <p>{_esc(content["summary"])}</p>\n  </section>'
        )

    experience = content.get("experience") or []
    if experience:
        entries = []
        for job in experience:
            bullets = "".join(f"<li>{_esc(b)}</li>" for b in job.get("bullets") or [])
            entries.append(
                f'<div class="entry">\n'
                f'      <div class="entry-header"><span>{_esc(job.get("title"))}</span>'
                f'<span>{_esc(job.get("dates"))}</span></div>\n'
                f'      <div class="entry-subheader"><span>{_esc(job.get("company"))}</span>'
                f'<span>{_esc(job.get("location"))}</span></div>\n'
                f'      <ul>{bullets}</ul>\n'
                f'    </div>'
            )
        sections.append(
            f'<section>\n    <h2>Experience</h2>\n    ' + "\n    ".join(entries) + '\n  </section>'
        )

    education = content.get("education") or []
    if education:
        entries = []
        for edu in education:
            entries.append(
                f'<div class="entry">\n'
                f'      <div class="entry-header"><span>{_esc(edu.get("degree"))}</span>'
                f'<span>{_esc(edu.get("dates"))}</span></div>\n'
                f'      <div class="entry-subheader"><span>{_esc(edu.get("school"))}</span>'
                f'<span>{_esc(edu.get("location"))}</span></div>\n'
                f'    </div>'
            )
        sections.append(
            f'<section>\n    <h2>Education</h2>\n    ' + "\n    ".join(entries) + '\n  </section>'
        )

    skills = content.get("skills") or []
    if skills:
        groups = []
        for group in skills:
            items = ", ".join(_esc(i) for i in group.get("items") or [])
            category = group.get("category")
            cat_html = f'<span class="skill-cat">{_esc(category)}:</span> ' if category else ""
            groups.append(f'<p class="skill-group">{cat_html}{items}</p>')
        sections.append(
            f'<section>\n    <h2>Skills</h2>\n    ' + "\n    ".join(groups) + '\n  </section>'
        )

    languages = content.get("languages") or []
    if languages:
        spans = "".join(f"<span>{_esc(lang)}</span>" for lang in languages)
        sections.append(
            f'<section>\n    <h2>Languages</h2>\n    <p class="skills-list">{spans}</p>\n  </section>'
        )

    for extra in content.get("extra_sections") or []:
        items = "".join(f"<li>{_esc(i)}</li>" for i in extra.get("items") or [])
        sections.append(
            f'<section>\n    <h2>{_esc(extra.get("heading"))}</h2>\n    <ul>{items}</ul>\n  </section>'
        )

    body = f'\n  {body_top}\n\n  ' + "\n\n  ".join(sections) + '\n\n'
    return f"{head}<body>{body}</body>\n</html>\n"


def fill_cover_letter_html(content: dict) -> str:
    head = _load_head("cover_letter.html")

    contact = _contact_spans(content.get("email"), content.get("phone"), content.get("location"))
    paragraphs = "\n\n  ".join(
        f'<p class="body-para">{_esc(p)}</p>' for p in content.get("paragraphs") or []
    )

    body = (
        f'\n  <div class="name">{_esc(content.get("full_name"))}</div>\n'
        f'  <div class="contact">{contact}</div>\n\n'
        f'  <div class="date">{_esc(content.get("date"))}</div>\n\n'
        f'  <div class="salutation">{_esc(content.get("salutation"))}</div>\n\n'
        f'  {paragraphs}\n\n'
        f'  <div class="closing">{_esc(content.get("closing"))}</div>\n'
        f'  <div class="signature">{_esc(content.get("signature_name"))}</div>\n\n'
    )
    return f"{head}<body>{body}</body>\n</html>\n"
