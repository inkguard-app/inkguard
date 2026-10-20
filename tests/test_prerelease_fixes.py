"""Found while checking the package before the first public release:
running the shipped example notes on a fresh install stopped
two of them, and a stress run showed the same kinds of stop.

Each test is the smallest sentence that showed the problem."""
from inkguard import contract


def _run(text):
    return contract.deidentify(text, mode="mask")


def test_a_middle_initial_t_does_not_make_an_ssn_a_temperature():
    # "T 900" read as a temperature of 900 protected the SSN as a vital sign.
    r = _run('On exam his dog tag reads "HOLLOWAY WYATT T 900-14-7708 O POS", '
             'so I verified blood type.')
    assert "900-14-7708" not in r.clean_text
    assert "[SSN]" in r.clean_text


def test_real_vital_signs_are_still_kept():
    text = "Vitals: T 98.6, HR 102, BP 150/96, RR 18, SpO2 98%. Temp 101.3 overnight."
    assert _run(text).clean_text == text


def test_a_bare_local_phone_number_holds_the_note():
    # Detection takes a seven-digit number only beside a contact word, so a
    # number standing alone must at least stop the note for a person.
    for text in ("Called HR 555-0147 about his leave.", "Wife reachable at 555-0147."):
        r = _run(text)
        assert r.status in ("review", "stop"), (text, r.status)
        assert any(w["word"] == "555-0147" for w in r.words_to_check)


def test_a_dose_range_does_not_hold_the_note():
    r = _run("Metformin titrated 500-1000 mg daily.")
    assert r.status == "clear"
    assert "500-1000 mg" in r.clean_text


def test_two_dots_in_a_row_are_not_a_web_address():
    # "12/18/2074...no" was read as the site "2074...no"; after the date took
    # "2074" the rest was ruled out but the original detection was reported
    # lost, and the whole file stopped.
    r = _run("Started there exactly one year ago (12/18/2074...no, that was yesterday).")
    assert r.status != "stop", r.raw.error


def test_real_web_addresses_are_still_removed():
    r = _run("Records at www.lopezfamily.example and https://portal.example.org/x.")
    assert "lopezfamily" not in r.clean_text and "portal.example" not in r.clean_text


def test_every_shipped_example_note_is_processed():
    """The first thing a new user runs. No example may stop."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "examples" / "sample-notes"
    stopped = []
    for p in sorted(root.rglob("*.txt")):
        r = _run(p.read_text(encoding="utf-8"))
        if r.status == "stop":
            stopped.append((p.relative_to(root).as_posix(), r.raw.error))
    assert stopped == []


def test_the_quick_command_says_no_person_has_reviewed_its_output(tmp_path):
    """A person reviews every output, including notes with no flags.
    The quick command does not run a review, so every output says so, and
    the report says plainly what is done, waiting, not processed, and that
    nothing is ready to share."""
    import json
    from pathlib import Path
    from inkguard import cli
    src = Path(__file__).resolve().parent.parent / "examples" / "sample-notes" / "patient-A"
    out = tmp_path / "out"
    cli.main([str(src), "-o", str(out), "--quiet"])
    notice = (out / cli.NOTICE_FILE).read_text(encoding="utf-8")
    assert "INCOMPLETE REVIEW" in notice and "NOT READY TO SHARE" in notice
    assert "No person has checked it" in notice
    md = " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.AUDIT_FILE).split())
    assert "facts only" in md and "not a certification" in md and "A person must check every output" in md
    for pile in (cli.PILE_DONE, cli.PILE_FLAGGED, cli.PILE_NOT):
        assert pile in md
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    assert report["human_review"]["done"] is False
    assert report["status"]["ready_to_share"] == 0


def test_the_guide_explains_everything_and_acknowledging_approves_nothing(tmp_path):
    """The guide is written for hospital staff with no time to spare. It says what
    happened, what each file is, what to do and not do, and offers an
    acknowledgement that records reading it without approving any file."""
    from pathlib import Path
    from inkguard import cli
    src = Path(__file__).resolve().parent.parent / "examples" / "sample-notes" / "patient-A"
    out = tmp_path / "out"
    cli.main([str(src), "-o", str(out), "--quiet"])
    g = (out / cli.NOTICE_FILE).read_text(encoding="utf-8")
    for heading in ("WHAT HAPPENS TO YOUR FILES", "THE OTHER THINGS IN THIS FOLDER", "WHAT TO DO NEXT",
                    "WHAT TO KEEP", "WHAT NOT TO DO", "IMPORTANT", "ACKNOWLEDGEMENT"):
        assert heading in g
    for tag in (cli.TAG_DONE, cli.TAG_FLAGGED, cli.TAG_NOT):
        assert f"Tag: {tag}__" in g
    assert "does NOT approve any file" in g and "WATERMARK" in g
    assert f"Never share {cli.PILE_NOT}" in g


def _named_input(tmp_path):
    from pathlib import Path
    import shutil
    ex = Path(__file__).resolve().parent.parent / "examples" / "sample-notes"
    src = tmp_path / "in"
    shutil.copytree(ex / "patient-A", src / "Garcia, Rosa")
    (src / "Garcia, Rosa" / "photo.jpg").write_bytes(b"x")
    return src


def test_a_dry_run_writes_nothing_and_opens_nothing(tmp_path, capsys):
    from inkguard import cli
    src = _named_input(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert cli.main([str(src), "-o", str(tmp_path / "clean"), "--dry-run"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "an image or scan" in out


def test_the_output_manifest_fingerprints_every_file_with_neutral_names(tmp_path):
    import csv
    import hashlib
    from inkguard import cli
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    text = (out / cli.TECH_DIR / cli.MANIFEST_FILE).read_text(encoding="utf-8")
    assert "Garcia" not in text and "Rosa" not in text
    rows = list(csv.DictReader(text.splitlines()))
    listed = {r["file"] for r in rows}
    # Everything but the originals' copies (pile 3) and the list itself.
    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*")
               if p.is_file() and p.name != cli.MANIFEST_FILE
               and p.relative_to(out).parts[0] != cli.PILE_NOT}
    assert listed == on_disk
    for r in rows:
        assert hashlib.sha256((out / r["file"]).read_bytes()).hexdigest() == r["sha256"]


def test_the_originals_are_checked_unchanged_after_the_run(tmp_path):
    import hashlib
    import json
    from inkguard import cli
    src = _named_input(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    assert before == after
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    assert report["status"]["originals_unchanged_checked_by_sha256"] is True
    # Running again over the same output must not call our own files leftovers.
    cli.main([str(src), "-o", str(out), "--quiet", "--resume"])
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    files = report["files"]
    assert files["every_file_accounted_for"] is True, files["problems"]
    assert not any("left over" in p for p in files["problems"])


def test_every_text_and_word_output_carries_its_review_status(tmp_path):
    """The provenance line travels with the file: method, review status and
    the run id that ties it to the manifest. An unreviewed file must never
    name the method without its status."""
    import csv
    import json
    import docx
    from inkguard import cli, lapis
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    run = report["run_id"]
    assert run and report["provenance_line"]["files_stamped"] > 0
    seen = 0
    for f in out.rglob("*"):
        rel = f.relative_to(out).as_posix()
        if not f.is_file() or not rel.startswith((cli.PILE_DONE + "/", cli.PILE_FLAGGED + "/")) \
                or f.name == cli.WORDS_FILE:
            continue
        if f.suffix in (".txt",):
            first = f.read_text(encoding="utf-8").split("\n", 1)[0]
        elif f.suffix == ".docx":
            first = docx.Document(str(f)).paragraphs[0].text
        else:
            continue
        seen += 1
        assert lapis.is_line(first), rel
        assert f"Run {run}" in first and "Safe Harbor" in first
        want = lapis.PENDING if rel.startswith(cli.PILE_FLAGGED + "/") else lapis.INCOMPLETE
        assert want in first, (rel, first)
        assert "NOT READY TO SHARE" in first
        assert "Garcia" not in first and "Rosa" not in first
    assert seen >= 3
    rows = list(csv.DictReader((out / cli.TECH_DIR / cli.MANIFEST_FILE).read_text(encoding="utf-8").splitlines()))
    assert {r["run_id"] for r in rows} == {run}


def test_no_header_leaves_the_text_exactly_as_cleaned(tmp_path):
    import json
    from inkguard import cli, lapis
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet", "--no-header"])
    for f in out.rglob("*.txt"):
        if f.name not in (cli.NOTICE_FILE, cli.PILE_NOTE):
            assert not f.read_text(encoding="utf-8").startswith(lapis.PREFIX)
    report = json.loads((out / ".inkguard" / "audit_trail.json").read_text(encoding="utf-8"))
    assert report["provenance_line"]["turned_off"] is True


def test_the_tool_skips_its_own_line_when_it_reads_a_file_back():
    from inkguard import lapis
    line = lapis.line(version="0.25.0", profile="safe_harbor", status=lapis.INCOMPLETE,
                      run_id="20261017-ABCD")
    assert line == ("Inkguard 0.25.0 · Safe Harbor · INCOMPLETE REVIEW · NOT READY TO SHARE · "
                    "Run 20261017-ABCD")
    assert lapis.strip(line + "\nPatient seen today.") == "Patient seen today."
    # Only a real provenance line is skipped, never a note's own first line.
    assert lapis.strip("Inkguard is a word here\nmore") == "Inkguard is a word here\nmore"
    assert lapis.strip("Patient seen today.") == "Patient seen today."


def test_the_problem_report_holds_nothing_from_the_files(tmp_path):
    """The feedback report (cli.FEEDBACK_FILE) is meant to be emailed, so
    it may carry only the version, run id, computer type, counts and codes."""
    import re
    from inkguard import cli
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    text = __import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.FEEDBACK_FILE)
    assert cli.REPORT_EMAIL in text and "holds no patient information" in text
    # Nothing from the input: no folder or file name, no word of any note.
    assert "Garcia" not in text and "Rosa" not in text and "photo" not in text
    note_words = set()
    for f in src.rglob("*.txt"):
        note_words |= {w for w in re.findall(r"[A-Za-z]{6,}", f.read_text(encoding="utf-8"))}
    allowed = set(re.findall(r"[A-Za-z]{6,}", cli.problem_report_text.__doc__ or ""))
    template = {"REPORT", "PROBLEM", "Inkguard", "between", "contains", "patient", "information",
                "record", "numbers", "pictures", "Describe", "problem", "instead", "Computer",
                "Python", "Method", "Harbor", "incomplete", "review", "pending", "processing",
                "stopped", "Support", "happened", "expected", "Copy", "everything", "inkguard",
                "project", "gmail", "listed", "release", "explained", "SUPPORT_CODES",
                "individually", "report", "notes", "made", "Keep", "found", "label", "files"}
    leaked = {w for w in re.findall(r"[A-Za-z]{6,}", text)} & note_words - allowed - template
    # Words the template itself uses are fine; anything else from a note is not.
    from inkguard.cli_review import REVIEW_REASONS
    fixed = (cli.problem_report_text(cli.RunStats())
             + " ".join(d for _, d in REVIEW_REASONS.values())
             + " ".join(cli._PLAIN_NAMES.values()))
    template_words = set(re.findall(r"[A-Za-z]{6,}", fixed))
    assert leaked - template_words == set(), leaked - template_words


def test_years_that_reveal_an_age_over_89_are_removed():
    """Safe Harbor removes 'all elements of dates (including
    year) indicative of' an age over 89. A birth year beside the note's own
    year gives the age back."""
    r = _run("PROGRESS NOTE 03/14/2071\nPatient seen. DOB 11/02/1979, a 91 year old woman with CHF.")
    assert "1979" not in r.clean_text and "90 or older" in r.clean_text
    assert "2071" in r.clean_text              # the note's own year may stay
    assert "1979" not in _run("PROGRESS NOTE 03/14/2071\nDOB: 11/02/1979. Follow up.").clean_text
    assert "1931" not in _run("Note 2026. Born 1931. Hip fracture.").clean_text


def test_ordinary_patients_keep_their_years():
    text = "Note 2026. DOB 1960. Diabetes since 2001, CABG 2015."
    assert _run(text).clean_text == text
    assert _run("Started metformin 2010; HbA1c 7.2 in 2024.").clean_text == \
        "Started metformin 2010; HbA1c 7.2 in 2024."


def test_a_date_field_does_not_swallow_the_rest_of_the_line():
    """'DOB: 11/02/1979. Follow up.' used to lose 'Follow up.'"""
    assert _run("DOB: 11/02/1979. Follow up.").clean_text == "DOB: 1979. Follow up."
    assert "cough" in _run("DOB: Nov 2, 1979. Seen for cough.").clean_text
    # Anything that does not start with a clear date is still removed whole.
    assert "Rosa" not in _run("DOB: 1979 per mother, Rosa Lopez").clean_text


def test_sample_file_misses_found_by_the_critics():
    """Misses found in the shipped example notes."""
    assert "47.2529" not in _run("loc 47.2529, -122.4443 | device on wrist").clean_text
    assert "(95 y.o.)" not in _run("her brother (95 y.o.) lives nearby.").clean_text
    out = _run("wife's (614) 555-O159 and cell 555-0l23.").clean_text
    assert "O159" not in out and "0l23" not in out
    # Clinical words before a date stay; the month goes.
    out = _run("GUM INFLAMATION IN DEC 2084. LAST FILLING JAN 2088.").clean_text
    assert "LAST FILLING" in out and "JAN" not in out and "DEC" not in out


def test_numbers_that_look_like_coordinates_or_phones_but_are_not():
    for text in ("Potassium 4.1, sodium 139, creatinine 1.02.", "Dose 0.125 mg, 0.250 mg",
                 "Room 555-Old wing", "a 67 y.o. male with CP"):
        assert _run(text).clean_text == text, text


def test_date_shifted_output_never_calls_itself_safe_harbor(tmp_path, monkeypatch):
    """Shifted dates keep a day and month, which Safe Harbor
    does not allow, so neither the file label nor the report may say so."""
    from inkguard import cli
    monkeypatch.setenv("PHI_ROOT_KEY", "ab" * 32)
    monkeypatch.setenv("HOME", str(tmp_path))
    # --date-shift sets these for the process; registered here so they are
    # removed again after the test and do not shift dates in later tests.
    monkeypatch.setenv("PHI_DATE_SHIFT", "")
    monkeypatch.setenv("PHI_DATE_SHIFT_MAX_DAYS", "")
    src = tmp_path / "in" / "p1"
    src.mkdir(parents=True)
    (src / "n.txt").write_text("Progress note 03/14/2071. DOB 04/25/1979. Follow up 04/20/2071.\n")
    out = tmp_path / "out"
    cli.main([str(tmp_path / "in"), "-o", str(out), "--date-shift", "--quiet"])
    stamped = next(out.rglob("*n.txt")).read_text(encoding="utf-8").split("\n", 1)[0]
    assert "Dates shifted (not Safe Harbor)" in stamped and "· Safe Harbor ·" not in stamped
    md = " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.AUDIT_FILE).split())
    assert "not Safe Harbor de-identified" in md


def test_the_report_never_calls_unreviewed_output_de_identified(tmp_path):
    """Output nobody has reviewed is never called de-identified."""
    from pathlib import Path
    from inkguard import cli
    src = Path(__file__).resolve().parent.parent / "examples" / "sample-notes" / "patient-A"
    out = tmp_path / "out"
    cli.main([str(src), "-o", str(out), "--quiet"])
    md = " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(out / cli.AUDIT_FILE).split())
    assert "Output was produced under the Safe Harbor method" not in md
    assert "not a certification" in md and "A person must check every output" in md
    assert "— de-identified " not in md
    assert "output is not PHI" not in cli.build_parser().format_help()


def test_the_feedback_check_passes_answers_and_stops_patient_details(tmp_path):
    import re
    """The note can be answered in its own words and sent; a line that looks
    like a patient detail is shown and must be changed first; an edited
    tool part is scanned like everything else."""
    from inkguard import cli
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    from inkguard import cli_docs
    note = cli_docs.document_text(out / cli.FEEDBACK_FILE)
    assert cli.check_feedback(note) == []                          # as written: ready to send
    box = cli_docs.FEEDBACK_HINT + "\n"
    assert box in note
    answered = note.replace(box, box + "A 7-digit number after the word enc was kept.\n")
    contact = answered.replace("Name: \n", "Name: Sam Sample\n").replace("Email: \n", "Email: sam@example.org\n")
    assert contact != answered
    assert cli.check_feedback(contact) == []                         # the sender's own details pass
    assert cli.check_feedback(answered.replace("Name: \n", "Name: MRN 4417203\n"))     # a record number does not
    assert cli.check_feedback(answered) == []
    risky = answered.replace("was kept.\n", "was kept.\nMrs. Delacroix called from 555-0182.\n")
    assert [line for _, line in cli.check_feedback(risky)] == ["Mrs. Delacroix called from 555-0182."]
    edited = re.sub(r"(?m)^(Codes +)none$", r"\1none, see Teodora Vlasic", note)
    assert edited != note and any("Teodora" in line for _, line in cli.check_feedback(edited))
    assert cli.main(["--check-feedback", str(out / cli.FEEDBACK_FILE)]) == 0


def test_every_run_says_how_to_cite_and_where_to_send_problems_and_thanks(tmp_path, capsys):
    import re
    from inkguard import cli
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out)])
    err = capsys.readouterr().err
    assert cli.REPORT_EMAIL in err and cli.CITE_FILE in err
    assert "Hall of Impact" not in err
    cite = (out / cli.CITE_FILE).read_text(encoding="utf-8")
    assert "@software{wajahat_inkguard_" in cite and cli._fingerprint() in cite
    guide = (out / cli.NOTICE_FILE).read_text(encoding="utf-8")
    assert "PROBLEMS AND THANKS" in guide and cli.CITE_FILE in guide
    # Nothing from the input files reaches the citation file.
    words = set()
    for f in src.rglob("*.txt"):
        words |= set(re.findall(r"[A-Za-z]{6,}", f.read_text(encoding="utf-8")))
    fixed = set(re.findall(r"[A-Za-z]{6,}", cli.cite_text({"methods_paragraph_draft": cli._methods(cli.RunStats(), False)})))
    assert (set(re.findall(r"[A-Za-z]{6,}", cite)) & words) - fixed == set()


# ---------------------------------------------- the results folder

def test_every_file_received_comes_out_in_exactly_one_tagged_folder(tmp_path):
    """Three folders only hold input material, every file in one carries the
    folder's tag, and the counts in the audit trail add up."""
    import json
    from inkguard import cli
    src = _named_input(tmp_path)
    (src / ".DS_Store").write_bytes(b"x")
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    received = [p for p in src.rglob("*") if p.is_file()]
    piled = []
    for pile in (cli.PILE_DONE, cli.PILE_FLAGGED, cli.PILE_NOT):
        for f in (out / pile).rglob("*"):
            if f.is_file() and f.name not in (cli.WORDS_FILE, cli.KEY_FILE, cli.PILE_NOTE) \
                    and not f.name.endswith(".pdf.txt"):
                assert f.name.startswith(cli.PILE_TAG[pile] + "__"), f
                piled.append(f)
    assert len(piled) == len(received)
    top = {p.name for p in out.iterdir()}
    assert top == {cli.GUIDE_FILE, cli.PILE_DONE, cli.PILE_FLAGGED, cli.PILE_NOT, cli.AUDIT_FILE,
                   cli.CITE_FILE, cli.FEEDBACK_FILE, cli.TECH_DIR} - ({cli.PILE_DONE} - top)
    p = json.loads((out / cli.TECH_DIR / cli.REPORT_JSON).read_text())["piles"]
    assert p["done"] + p["flagged"] + p["not_processed"] == p["received"] == len(received)
    assert p["every_file_in_exactly_one"] is True


def test_every_document_has_the_house_header_and_footer(tmp_path):
    """The audit trail, the citations and the feedback
    report always open and close the same way."""
    from inkguard import cli
    src = _named_input(tmp_path)
    out = tmp_path / "clean"
    cli.main([str(src), "-o", str(out), "--quiet"])
    from inkguard import cli_docs
    audit = cli_docs.document_text(out / cli.AUDIT_FILE)
    cite = (out / cli.CITE_FILE).read_text(encoding="utf-8")
    fb = cli_docs.document_text(out / cli.FEEDBACK_FILE)
    assert "Inkguard · Audit trail" in audit and "End of audit trail." in audit
    assert cite.startswith("INKGUARD · CITATIONS\nRun ") and cite.rstrip().endswith("End of citations.")
    assert fb.lstrip().startswith("Inkguard · Feedback report") and "End of feedback report." in fb
    for text in (" ".join(audit.split()), cite, fb):
        assert "About this document. Written by Inkguard" in text and "Apache License 2.0" in text
    assert fb.index(cli_docs.FEEDBACK_BOX) < fb.index(cli_docs.CONTACT_BOX) < fb.index(cli_docs.SYSTEM_HEAD)
    assert fb.index(f"Please email this report to {cli.REPORT_EMAIL}.") < fb.index(cli_docs.FEEDBACK_BOX)
    for block in ("[1] APA", "[2] AMA", "[3] Vancouver", "[4] BibTeX", "[5] Methods paragraph"):
        assert block in cite

def test_the_guide_in_the_download_is_the_guide_every_run_writes(tmp_path):
    from pathlib import Path
    from inkguard import cli, cli_report
    root = Path(__file__).resolve().parent.parent
    guide = (root / "GUIDE.txt").read_text(encoding="utf-8")
    assert guide == cli_report.download_guide(), \
        "regenerate GUIDE.txt: python -c 'from inkguard import cli_report; ...download_guide()'"
    src = tmp_path / "in" / "p1"
    src.mkdir(parents=True)
    (src / "n.txt").write_text("Patient seen, well.\n")
    cli.main([str(src.parent), "-o", str(tmp_path / "out"), "--quiet"])
    assert (tmp_path / "out" / cli.GUIDE_FILE).read_text(encoding="utf-8") == guide


def test_the_local_audit_trail_details_are_optional_and_recorded_as_entered(tmp_path):
    from inkguard import cli
    src = tmp_path / "in" / "p1"
    src.mkdir(parents=True)
    (src / "n.txt").write_text("Patient seen, well.\n")
    cli.main([str(src.parent), "-o", str(tmp_path / "out"), "--quiet"])          # nothing given: runs
    md = " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(tmp_path / "out" / cli.AUDIT_FILE).split())
    assert "Principal investigator not given" in md
    cli.main([str(src.parent), "-o", str(tmp_path / "out2"), "--quiet", "--pi", "Dr. Test"])
    md = " ".join(__import__('inkguard.cli_docs', fromlist=['x']).document_text(tmp_path / "out2" / cli.AUDIT_FILE).split())
    assert "Principal investigator Dr. Test" in md

def test_files_not_processed_carry_no_watermark_and_are_exact_copies(tmp_path):
    """The third folder holds what the software did not or
    could not read, so nothing of ours is written into those files; only the
    tag in the name says where they came from."""
    import hashlib
    from inkguard import cli, lapis
    src = tmp_path / "in"
    (src / "z").mkdir(parents=True)
    (src / "z" / "long.txt").write_text("Patient seen. " + "a" * 12000 + "\n")   # stopped, set aside
    (src / "z" / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    cli.main([str(src), "-o", str(tmp_path / "out"), "--quiet"])
    pile = tmp_path / "out" / cli.PILE_NOT
    copies = [f for f in pile.rglob("*") if f.name.startswith(cli.TAG_NOT + "__")]
    assert len(copies) == 2
    for f in copies:
        orig = src / "z" / f.name[len(cli.TAG_NOT) + 2:]
        assert hashlib.sha256(f.read_bytes()).digest() == hashlib.sha256(orig.read_bytes()).digest()
        assert not f.read_bytes().startswith(lapis.PREFIX.encode())
