"""The welcome page, the code fingerprint, the Methods draft and the
installation self-test."""
import shutil

from inkguard import cli, granite, fluorite


def test_the_fingerprint_is_stable_and_changes_when_the_code_does(tmp_path, monkeypatch):
    a = fluorite.code_fingerprint()
    assert a == fluorite.code_fingerprint() and len(a) == 64
    copy = tmp_path / "inkguard"
    shutil.copytree(fluorite.APP_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(fluorite, "APP_DIR", copy)
    assert fluorite.code_fingerprint() == a                 # caches do not count
    (copy / "policy.py").write_text((copy / "policy.py").read_text() + "\n# changed\n")
    assert fluorite.code_fingerprint() != a


def test_the_methods_draft_names_what_ran_and_leaves_the_review_to_the_team():
    fp = "ab" * 32
    t = fluorite.methods_text(version="9.9.9", profile="safe_harbor", date_shifted=False,
                          fingerprint=fp, run_id="20261017-ABCD")
    assert "9.9.9" in t and fp in t and "20261017-ABCD" in t and "164.514(b)(2)" in t
    assert fluorite.HELD_OUT in t and "not been validated on real clinical records" in t
    assert "[number and role of reviewers" in t and "IRB" in t
    lds = fluorite.methods_text(version="1", profile="limited_data_set", date_shifted=False, fingerprint=fp)
    assert "remain protected health information" in lds and "data use agreement" in lds
    shifted = fluorite.methods_text(version="1", profile="safe_harbor", date_shifted=True, fingerprint=fp)
    assert "does not meet the Safe Harbor method" in shifted


def test_the_headline_figure_is_the_one_the_review_screens_show():
    assert any(fluorite.HELD_OUT in line["result"] for line in granite.ACCURACY["lines"])


def test_every_report_carries_the_fingerprint_and_the_methods_draft(tmp_path):
    import json
    src = tmp_path / "in" / "p1"
    src.mkdir(parents=True)
    (src / "n.txt").write_text("Patient seen, well.\n")
    out = tmp_path / "out"
    cli.main([str(src.parent), "-o", str(out), "--quiet"])
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    assert report["software"]["code_fingerprint_sha256"] == fluorite.code_fingerprint()
    assert report["run_id"] in report["methods_paragraph_draft"]
    md = "".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.AUDIT_FILE).split())
    assert fluorite.code_fingerprint() in md
    cite = (out / cli.CITE_FILE).read_text(encoding="utf-8")
    assert "Methods paragraph" in cite and fluorite.code_fingerprint() in cite and report["run_id"] in cite


def test_about_and_self_test_need_no_folder(capsys):
    assert cli.main(["--about"]) == 0
    assert "WHAT IT DOES NOT DO" in capsys.readouterr().out
    assert cli.main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out and "0 attempts" in out


def test_the_self_test_fails_loudly_when_identifiers_survive(monkeypatch, capsys):
    monkeypatch.setattr(fluorite, "_MUST_GO", fluorite._MUST_GO + ("metformin",))
    passed, lines = fluorite.self_test()
    assert not passed and any(line.startswith("  FAIL  known identifiers") for line in lines)


def test_the_standing_notice_is_on_every_surface(tmp_path):
    """The reminder that this is an automated pipeline that can misread
    context, and that a person must check everything, is shown at all times."""
    import json
    import re
    from pathlib import Path
    from inkguard import halite
    v = fluorite.VERIFY_NOTICE
    root = Path(__file__).resolve().parent.parent
    assert v in fluorite.about()
    assert v in "\n".join(fluorite.self_test()[1])
    src = tmp_path / "in" / "p1"
    src.mkdir(parents=True)
    (src / "n.txt").write_text("Patient seen, well.\n")
    out = tmp_path / "out"
    cli.main([str(src.parent), "-o", str(out), "--quiet"])
    assert v in (out / cli.NOTICE_FILE).read_text(encoding="utf-8")
    assert " ".join(v.split()) in " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.AUDIT_FILE).split())
    assert json.loads((out / ".inkguard" / "audit_trail.json").read_text())["verify_notice"] == v
    assert v in (root / "inkguard" / "chert_page.html").read_text(encoding="utf-8")
    js = (root / "desktop" / "ui" / "js" / "ui.js").read_text(encoding="utf-8")
    m = re.search(r'export const VERIFY_NOTICE =\s*"([^"]+)"', js)
    assert m and m.group(1) == v
    readme = (root / "README.md").read_text(encoding="utf-8")
    readme = re.sub(r"\s+", " ", re.sub(r"(?m)^>\s?", "", readme).replace("**", ""))
    assert v in readme
    assert "VERIFY_NOTICE" in Path(halite.__file__).read_text(encoding="utf-8")
