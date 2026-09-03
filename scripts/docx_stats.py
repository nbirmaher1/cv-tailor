#!/usr/bin/env python3
"""Print quick length stats for a tailored CV JSON content record, as a length-flag proxy
for DOCX output (which has no page-count signal). Read-only, no rendering."""
import json
import re
import sys
from pathlib import Path


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def compute_stats(json_path):
    data = json.loads(Path(json_path).read_text())
    bullets = [b for exp in data.get("experience", []) for b in exp.get("bullets", [])]
    bullet_words = sum(word_count(b) for b in bullets)
    summary_words = word_count(data.get("summary", ""))
    skills_words = sum(word_count(item) for grp in data.get("skills", []) for item in grp.get("items", []))
    extra_words = sum(word_count(item) for sec in data.get("extra_sections", []) for item in sec.get("items", []))
    total_narrative_words = summary_words + bullet_words + extra_words

    return {
        "experience_entries": len(data.get("experience", [])),
        "total_bullets": len(bullets),
        "summary_words": summary_words,
        "bullet_words": bullet_words,
        "extra_words": extra_words,
        "skills_words": skills_words,
        "total_narrative_words": total_narrative_words,
    }


def main(json_path):
    stats = compute_stats(json_path)
    print(f"experience entries: {stats['experience_entries']}")
    print(f"total bullets: {stats['total_bullets']}")
    print(f"summary words: {stats['summary_words']}")
    print(f"bullet words: {stats['bullet_words']}")
    print(f"extra-section words: {stats['extra_words']}")
    print(f"skills words (not counted toward narrative total): {stats['skills_words']}")
    print(f"total narrative words (summary+bullets+extra): {stats['total_narrative_words']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: docx_stats.py <content.json>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
