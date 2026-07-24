import json

from docx_stats import main, word_count


def test_word_count_counts_whitespace_separated_tokens():
    assert word_count("Led a team of 5 engineers") == 6
    assert word_count("") == 0
    assert word_count(None) == 0


def test_main_reports_expected_totals(tmp_path, capsys):
    content = {
        "experience": [{"bullets": ["Led a team of five", "Cut costs by 10%"]}],
        "summary": "Data analyst with five years experience.",
        "skills": [{"category": None, "items": ["SQL", "Python"]}],
        "extra_sections": [{"heading": "Certifications", "items": ["AWS Certified"]}],
    }
    json_path = tmp_path / "content.json"
    json_path.write_text(json.dumps(content))

    main(str(json_path))

    out = capsys.readouterr().out
    assert "experience entries: 1" in out
    assert "total bullets: 2" in out
    assert "summary words: 6" in out
    assert "bullet words: 9" in out
    assert "extra-section words: 2" in out
    assert "total narrative words (summary+bullets+extra): 17" in out
