#!/usr/bin/env python3
"""Print quick length stats for a tailored CV JSON content record, as a length-flag proxy
for DOCX output (which has no page-count signal). Read-only, no rendering."""
import json
import re
import sys
from pathlib import Path


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def main(json_path):
    data = json.loads(Path(json_path).read_text())
    bullets = [b for exp in data.get("experience", []) for b in exp.get("bullets", [])]
    bullet_words = sum(word_count(b) for b in bullets)
    summary_words = word_count(data.get("summary", ""))
    skills_words = sum(word_count(item) for grp in data.get("skills", []) for item in grp.get("items", []))
    extra_words = sum(word_count(item) for sec in data.get("extra_sections", []) for item in sec.get("items", []))
    total_narrative_words = summary_words + bullet_words + extra_words

    print(f"experience entries: {len(data.get('experience', []))}")
    print(f"total bullets: {len(bullets)}")
    print(f"summary words: {summary_words}")
    print(f"bullet words: {bullet_words}")
    print(f"extra-section words: {extra_words}")
    print(f"skills words (not counted toward narrative total): {skills_words}")
    print(f"total narrative words (summary+bullets+extra): {total_narrative_words}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: docx_stats.py <content.json>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
