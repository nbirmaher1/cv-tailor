"""Static checks on the frontend JS files that pytest (a Python runner) can
still usefully catch: none of these files are ES modules, so every
<script src=...> tag on the page shares ONE global scope. A top-level
`const`/`let`/`function` declared in two different files is a SyntaxError
the moment both load together -- and it silently kills the *entire* second
file (see the `pendingCard` bug: a `const pendingCard` in tailor.js collided
with a `function pendingCard` in applications.js, which broke the whole
Applications tab with no visible error). None of the existing tests load
real browser JS, so nothing else in this suite would catch a regression here.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"

# Only a top-level (column-0) declaration is a global-scope binding; anything
# indented is inside a function/block and doesn't collide across files.
# `const`/`let` create a genuine lexical binding that conflicts with ANY other
# top-level declaration of the same name (even a plain `function`) -- that's
# the exact `pendingCard` bug. Two plain `function` declarations of the same
# name across files are legal (last one loaded just wins), so that
# combination alone is *not* flagged -- see e.g. the shared `el()` DOM-helper
# convention already used identically in master-cv.js/applications.js/home.js.
TOP_LEVEL_DECL_RE = re.compile(r"^(const|let|function)\s+([A-Za-z_$][\w$]*)")


def _scripts_loaded_together() -> list:
    """The exact set of local <script src="/static/js/...​"> files index.html
    loads, in order -- i.e. the files that actually share one global scope."""
    html = INDEX_HTML.read_text()
    return re.findall(r'<script src="/static/js/([\w.-]+\.js)"></script>', html)


def _top_level_declarations(js_path: Path) -> dict:
    """name -> set of declaration kinds ('const'/'let'/'function') used for it
    at the top level of this one file."""
    decls: dict = {}
    for line in js_path.read_text().splitlines():
        m = TOP_LEVEL_DECL_RE.match(line)
        if m:
            kind, name = m.group(1), m.group(2)
            decls.setdefault(name, set()).add(kind)
    return decls


def test_index_html_actually_loads_the_known_js_files():
    # Sanity check on the test itself: fail loudly if index.html's script
    # tags ever stop matching this test's expectations, rather than silently
    # checking zero files.
    scripts = _scripts_loaded_together()
    assert set(scripts) == {f.name for f in STATIC_JS_DIR.glob("*.js")}


def test_no_lexical_top_level_identifier_collides_across_shared_scripts():
    scripts = _scripts_loaded_together()
    # name -> {filename: {kinds declared there}}
    by_name: dict = {}
    for filename in scripts:
        for name, kinds in _top_level_declarations(STATIC_JS_DIR / filename).items():
            by_name.setdefault(name, {})[filename] = kinds

    collisions = []
    for name, per_file in by_name.items():
        if len(per_file) < 2:
            continue
        all_kinds = set().union(*per_file.values())
        if "const" in all_kinds or "let" in all_kinds:
            collisions.append(f"{name!r}: " + ", ".join(f"{f} ({'/'.join(sorted(k))})" for f, k in per_file.items()))

    assert not collisions, (
        "A const/let declaration must be globally unique across every script "
        "index.html loads together (they share one global scope, so a "
        "duplicate is a SyntaxError that silently kills the whole later "
        "file -- unlike two plain `function` declarations of the same name, "
        "which are legal):\n" + "\n".join(collisions)
    )
