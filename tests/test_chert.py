"""Three-person release: the tool redacts, then
two authorized reviewers each review every note twice (see-through, then
blacked out) without seeing each other's work, and the authorizing doctor
sees everything, decides the unsure bucket, and signs as the third, equal
reviewer. Two removes lock an item; two keeps keep it unless the doctor
removes it; everything else is the doctor's call, removed until decided.
Identifiers the tool is certain of can never be kept."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

import pytest  # noqa: E402

from inkguard import chert  # noqa: E402

NOTES = {
    "smith_a/2021-03-02_hp.txt": (
        "ADMISSION H&P\nPatient: Hubert Lindqvist   MRN: 4471822   DOB: 03/14/1951\n"
        "HPI: 72M with Fabry disease, retired schoolteacher, presents with chest pain.\n"
        "Daughter Rose Alvarado at bedside. Seen by Dr. Kellerman.\n"
        "Plan: barbotage discussed. Follow up 4/16/2021.\n"),
    "smith_a/2021-03-05_progress.txt": (
        "PROGRESS NOTE\nRose visited today. Pt tolerating diet. BP rose to 150 overnight.\n"
        "Call back number for daughter 415-555-0199.\n"),
    "zz_other/visit.txt": "CLINIC NOTE\nFollow-up visit. Wound clean, Steri-Strips in place. Return as needed.\n",
}
REVIEWERS = ["Jane Roe, RN", "Sam Poe"]
DOCTOR = "Dr. Ada Winterhalter"
TEMPLATE = Path(chert.__file__).with_name("chert_page.html")


def _prepare(tmp, notes, **kw):
    src = tmp / "notes"
    for rel, text in notes.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    return chert.prepare(src, tmp / "batches", authorized_by=kw.pop("authorized_by", DOCTOR),
                          reviewers=REVIEWERS, **kw)


@pytest.fixture()
def batch(tmp_path):
    dirs = _prepare(tmp_path, NOTES, batch_patients=200)
    return tmp_path, dirs[0], chert.load_batch(dirs[0])


def _text(b, n):
    return chert._sanitized((Path(b["source_dir"]) / n["source"]).read_text())


def decisions(b: dict, rid: str, *, keep=(), unsure=(), add=(), answers=None, signed=True,
              skip=(), seconds=30, feedback=None, flags=None):
    """A reviewer's file as the page exports it. keep / unsure: texts of tool
    removals; add: (note_index, start, end, type); answers: {text: answer}
    for questions and rare-disease flags; seconds: one number or
    {note_index: seconds}."""
    names = {r["id"]: r["name"] for r in b["reviewers"]}
    notes = {}
    for i, n in enumerate(b["notes"]):
        if n["status"] != "ready" or i in skip:
            continue
        text = _text(b, n)
        pick = lambda words: [r["id"] for r in n["removals"] if text[r["start"]:r["end"]] in words]  # noqa: E731
        st = {"looks": {"see": True, "blackout": True}, "confirmed_at": "2026-09-26T00:00:00Z",
              "seconds": seconds.get(i, 30) if isinstance(seconds, dict) else seconds,
              "keep": pick(keep), "unsure": pick(unsure),
              "add": [{"start": s, "end": e, "type": t} for (j, s, e, t) in add if j == i],
              "answers": {}}
        for q in n["questions"]:
            st["answers"][q["id"]] = (answers or {}).get(text[q["start"]:q["end"]], "fine")
        for f in n["rare"]:
            st["answers"][f["id"]] = (answers or {}).get(text[f["start"]:f["end"]], "ok")
        if flags and i in flags:
            st["fb"] = flags[i]
        notes[n["id"]] = st
    return {"kind": chert.DECISIONS_KIND, "version": 1, "role": "reviewer", "batch": b["batch"],
            "reviewer": rid, "reviewer_name": names[rid], "notes": notes,
            **({"feedback": feedback} if feedback is not None else {}),
            "signed_off": ({"at": "2026-09-26T01:00:00Z", "typed_name": names[rid],
                            "statement": chert.ATTESTATION} if signed else None)}


def write(tmp, name, obj, *, seal=True, key_for=None):
    """Write a file the way its page would: sealed with that page's key."""
    p = tmp / name
    if seal and obj.get("kind") == chert.DECISIONS_KIND:
        obj = chert.seal_file(obj, chert.page_key(obj["batch"], key_for or obj["reviewer"]))
    p.write_text(json.dumps(obj))
    return p


def pair(tmp, b, r1=None, r2=None, tag=""):
    return [write(tmp, f"r1{tag}.json", r1 or decisions(b, "R1")),
            write(tmp, f"r2{tag}.json", r2 or decisions(b, "R2"))]


def doctor_file(tmp, pairs, *, rulings=None, add=(), withhold=(), decide_bucket=True,
                name=DOCTOR, signed_at="2026-09-26T02:00:00Z", decided_at="2026-09-26T01:30:00Z",
                fname="doctor.json", seal=True, vendor=None, flags=None):
    """The doctor's file as the doctor's page exports it. pairs: [(batch,
    [reviewer files])]. rulings: {text: keep|remove}; every bucket item not
    named is removed when decide_bucket. add: (batch_index, note_index,
    start, end, type); withhold: {(batch_index, note_index)}."""
    batches = {}
    for bi, (b, files) in enumerate(pairs):
        dec = chert.load_decisions(b, files)
        notes = {}
        for i, n in enumerate(b["notes"]):
            if n["status"] != "ready":
                continue
            text = _text(b, n)
            items, _ = chert.note_items(n, dec["R1"]["notes"].get(n["id"]),
                                         dec["R2"]["notes"].get(n["id"]), text)
            r = {}
            for it in items:
                word = text[it["start"]:it["end"]]
                if rulings and word in rulings:
                    r[it["key"]] = rulings[word]
                elif it["bucket"] and decide_bucket:
                    r[it["key"]] = "remove"
            notes[n["id"]] = {"looks": {"see": True, "blackout": True}, "opened": True, "seconds": 20,
                              "rulings": r, "decided_at": {k: decided_at for k in r},
                              "add": [{"start": s, "end": e, "type": t, "at": decided_at}
                                      for (bj, j, s, e, t) in add if (bj, j) == (bi, i)],
                              "withhold": (bi, i) in withhold,
                              **({"fb": flags[(bi, i)]} if flags and (bi, i) in flags else {})}
        batches[b["batch"]] = {"reviewer_files": {rid: dec[rid]["_sha256"] for rid in ("R1", "R2")},
                               "notes": notes}
    pages = "+".join(b["batch"] for b, _ in pairs)
    obj = {"kind": chert.DOCTOR_KIND, "version": 1, "role": "doctor", "pages": pages,
           "reviewer": "DOC", "reviewer_name": name, "batches": batches,
           **({"vendor": vendor} if vendor is not None else {}),
           "signed_off": {"at": signed_at, "typed_name": name, "statement": chert.DOCTOR_ATTESTATION}}
    p = tmp / fname
    p.write_text(json.dumps(chert.seal_file(obj, chert.page_key(pages, "DOC")) if seal else obj))
    return p


def release(tmp, b, bdir, files, out="released", **kw):
    doc = doctor_file(tmp, [(b, files)], **kw)
    return chert.release(bdir, files, tmp / out, doctor_file=doc)["batches"][0]


def released(tmp, b, root="released"):
    outs = list((tmp / root).glob(f"batch-{b['number']:03d}-*"))
    if not outs:
        return {}
    out = outs[0]
    return {p.relative_to(out).as_posix(): p.read_text() for p in sorted(out.rglob("*.txt"))}


def one(tmp, pattern, root="released"):
    (p,) = list((tmp / root).glob(pattern))
    return p


def span(b, idx, word):
    t = _text(b, b["notes"][idx])
    s = t.index(word)
    return (idx, s, s + len(word))


def removal(b, idx, word):
    n = b["notes"][idx]
    t = _text(b, n)
    (r,) = [r for r in n["removals"] if t[r["start"]:r["end"]] == word]
    return r


# ---------------------------------------------------------------- prepare

def test_prepare_groups_by_patient_and_writes_one_page_per_reviewer(batch):
    tmp, bdir, b = batch
    assert [p["label"] for p in b["patients"]] == ["Patient 1", "Patient 2"]
    assert (bdir / "review-reviewer-1.html").exists() and (bdir / "review-reviewer-2.html").exists()
    assert all(n["status"] == "ready" for n in b["notes"])
    # batch.json holds offsets and types, never the notes' words.
    raw = (bdir / "batch.json").read_text()
    for word in ("Lindqvist", "Alvarado", "4471822", "chest pain", "Kellerman"):
        assert word not in raw


def test_a_batch_never_splits_a_patient(tmp_path):
    src = tmp_path / "n"
    for p in range(5):
        for k in range(3):
            (src / f"p{p}").mkdir(parents=True, exist_ok=True)
            (src / f"p{p}" / f"{k}.txt").write_text(f"Visit {k}. Knee pain, improving.\n")
    dirs = chert.prepare(src, tmp_path / "b", authorized_by="Dr. O", reviewers=REVIEWERS,
                          batch_patients=2)
    batches = [chert.load_batch(d) for d in dirs]
    assert [len(b["patients"]) for b in batches] == [2, 2, 1]
    for b in batches:
        assert all(len(p["notes"]) == 3 for p in b["patients"])


def test_two_different_reviewers_are_required(tmp_path):
    (tmp_path / "n").mkdir()
    (tmp_path / "n" / "a.txt").write_text("Knee pain.")
    with pytest.raises(chert.ReviewError):
        chert.prepare(tmp_path / "n", tmp_path / "b", authorized_by="Dr. O",
                       reviewers=["Jane Roe", "jane  roe"])
    with pytest.raises(chert.ReviewError):
        chert.prepare(tmp_path / "n", tmp_path / "b", authorized_by="Dr. O", reviewers=["Jane Roe"])


def test_the_tool_asks_about_words_it_does_not_understand_and_flags_rare_diseases(batch):
    tmp, bdir, b = batch
    hp = b["notes"][0]
    t = _text(b, hp)
    assert "barbotage" in [t[q["start"]:q["end"]] for q in hp["questions"]]
    rare = [(t[f["start"]:f["end"]], f["also"]) for f in hp["rare"]]
    assert rare and rare[0][0] == "Fabry disease" and "an age" in rare[0][1]
    assert "a job or school" in rare[0][1]


def test_identifiers_found_by_label_or_exact_format_are_certain(batch):
    tmp, bdir, b = batch
    assert removal(b, 0, "4471822")["certain"]                 # MRN
    assert removal(b, 0, "Hubert Lindqvist")["certain"]        # "Patient:" label
    assert removal(b, 1, "415-555-0199")["certain"]            # phone format
    assert not removal(b, 0, "Kellerman")["certain"]
    assert not removal(b, 0, "Rose Alvarado")["certain"]


def test_page_cannot_be_broken_by_note_text(tmp_path):
    (tmp_path / "n" / "p").mkdir(parents=True)
    (tmp_path / "n" / "p" / "a.txt").write_text(
        "Pt pasted </script><script>alert(1)</script> <!-- into chart.")
    d = chert.prepare(tmp_path / "n", tmp_path / "b", authorized_by="Dr. O", reviewers=REVIEWERS)[0]
    page = (d / "review-reviewer-1.html").read_text()
    data = page.split("const DATA = ", 1)[1].split("</script>", 1)[0]
    assert "<" not in data                      # every "<" inside the data is escaped
    assert "alert(1)" in data


def _page_data(path):
    page = Path(path).read_text()
    return json.loads(page.split("const DATA = ", 1)[1].split(";</script>", 1)[0])


def test_each_page_carries_only_its_own_seal_key(batch):
    tmp, bdir, b = batch
    k1 = _page_data(bdir / "review-reviewer-1.html")["seal"]
    k2 = _page_data(bdir / "review-reviewer-2.html")["seal"]
    assert k1 == chert.page_key(b["batch"], "R1").hex() and k2 == chert.page_key(b["batch"], "R2").hex()
    assert k1 != k2
    for page in ("review-reviewer-1.html", "review-reviewer-2.html"):
        assert os.environ["PHI_ROOT_KEY"] not in (bdir / page).read_text()


# ---------------------------------------------------------------- the three sign-offs

def test_release_needs_both_reviewers_and_the_doctor(batch):
    tmp, bdir, b = batch
    ok = write(tmp, "r1.json", decisions(b, "R1"))
    unsigned = write(tmp, "r2.json", decisions(b, "R2", signed=False))
    with pytest.raises(chert.ReviewError, match="signed off"):
        chert.doctor([bdir], [ok, unsigned], tmp / "doctor.html")
    partial = write(tmp, "r2b.json", decisions(b, "R2", skip={1}))
    with pytest.raises(chert.ReviewError, match="not confirmed 1"):
        chert.doctor([bdir], [ok, partial], tmp / "doctor.html")
    same = write(tmp, "r1b.json", decisions(b, "R1"))
    with pytest.raises(chert.ReviewError, match="two files"):
        chert.doctor([bdir], [ok, same], tmp / "doctor.html")
    files = pair(tmp, b)
    wrong = doctor_file(tmp, [(b, files)], name="Jane Roe, RN")
    with pytest.raises(chert.ReviewError, match="not signed by the person who authorized"):
        chert.release(bdir, files, tmp / "released", doctor_file=wrong)
    assert not released(tmp, b)


def test_a_file_from_someone_not_authorized_is_refused(batch):
    tmp, bdir, b = batch
    d = decisions(b, "R2")
    d["reviewer_name"] = "Somebody Else"
    with pytest.raises(chert.ReviewError, match="not one this batch authorized"):
        chert.load_decisions(b, [write(tmp, "a.json", decisions(b, "R1")), write(tmp, "b.json", d)])


def test_everyone_agreeing_with_the_tool_gives_the_engines_own_output(batch):
    tmp, bdir, b = batch
    rep = release(tmp, b, bdir, pair(tmp, b))
    out = released(tmp, b)
    assert rep["counts"]["released"] == 3
    hp = out["patient-0001/note-001.txt"]
    assert "Lindqvist" not in hp and "4471822" not in hp and "Kellerman" not in hp
    assert "DOB: 1951" in hp and "Fabry disease" in hp and "barbotage discussed" in hp
    # Neutral names; the way back to the sources is a separate local file.
    assert set(out) == {"patient-0001/note-001.txt", "patient-0001/note-002.txt",
                        "patient-0002/note-001.txt"}
    assert "smith_a" in one(tmp, "release_map-batch-001-*.csv").read_text()
    assert "smith_a" not in "".join(out)


def test_the_vote(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", keep={"Kellerman", "Rose Alvarado"},
                   add=[span(b, 0, "schoolteacher") + ("other",)])
    r2 = decisions(b, "R2", keep={"Kellerman"}, answers={"barbotage": "remove"})
    files = pair(tmp, b, r1, r2)
    dec = chert.load_decisions(b, files)
    n = b["notes"][0]
    items, _ = chert.note_items(n, dec["R1"]["notes"][n["id"]], dec["R2"]["notes"][n["id"]], _text(b, n))
    t = _text(b, n)
    by = {t[i["start"]:i["end"]]: i for i in items}
    assert by["Kellerman"]["default"] == "keep" and not by["Kellerman"]["bucket"]   # both kept
    assert by["Rose Alvarado"]["bucket"]                                           # one kept: the doctor decides
    assert by["schoolteacher"]["default"] == "remove" and not by["schoolteacher"]["bucket"]
    assert by["barbotage"]["default"] == "remove"
    assert by["4471822"]["locked"]
    # Without the doctor's decision on the bucket, nothing is released.
    undecided = doctor_file(tmp, [(b, files)], decide_bucket=False)
    with pytest.raises(chert.ReviewError, match="not decided by the doctor"):
        chert.release(bdir, files, tmp / "released", doctor_file=undecided)
    release(tmp, b, bdir, files, rulings={"Rose Alvarado": "keep"})
    hp = released(tmp, b)["patient-0001/note-001.txt"]
    assert "Dr. Kellerman" in hp               # both reviewers kept it
    assert "Rose Alvarado" in hp               # one kept it and the doctor agreed
    assert "schoolteacher" not in hp           # one reviewer marked it: removed unless the doctor keeps it
    assert "barbotage" not in hp               # one reviewer answered "remove"


def test_the_doctor_can_never_keep_what_both_removed_or_the_tool_is_certain_of(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b)
    for word in ("Rose Alvarado", "4471822"):
        doc = doctor_file(tmp, [(b, files)], rulings={word: "keep"}, fname=f"d-{word[:3]}.json")
        with pytest.raises(chert.ReviewError, match="keeps something both reviewers removed"):
            chert.release(bdir, files, tmp / "released", doctor_file=doc)
    assert not released(tmp, b)


def test_the_doctor_has_master_control(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b, decisions(b, "R1", keep={"Kellerman"}), decisions(b, "R2", keep={"Kellerman"}))
    rep = release(tmp, b, bdir, files, rulings={"Kellerman": "remove"},
                  add=[(0,) + span(b, 0, "chest pain") + ("other",)], withhold={(0, 2)})
    out = released(tmp, b)
    assert "Kellerman" not in out["patient-0001/note-001.txt"]      # both kept; the doctor removed
    assert "chest pain" not in out["patient-0001/note-001.txt"]     # the doctor's own mark
    assert "patient-0002/note-001.txt" not in out                   # withheld
    c = rep["counts"]
    assert c["withheld_by_doctor"] == 1 and c["doctor_marks"] == 1 and c["doctor_changed_default"] == 1
    assert "withheld by the doctor" in one(tmp, "release_map-batch-001-*.csv").read_text()
    assert rep["accuracy"]["doctor_marks"] == 1


def test_unsure_goes_to_the_doctor_and_counts_for_learning(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", unsure={"Kellerman"}, answers={"barbotage": "unsure"})
    r2 = decisions(b, "R2", add=[span(b, 0, "schoolteacher") + ("unsure",)],
                   answers={"Fabry disease": "unsure"})
    files = pair(tmp, b, r1, r2)
    rep = release(tmp, b, bdir, files, rulings={"Kellerman": "remove", "barbotage": "keep",
                                                "schoolteacher": "keep", "Fabry disease": "keep"})
    c = rep["counts"]
    assert c["bucket_items"] == 4 and c["bucket_kept_by_doctor"] == 3 and c["bucket_removed_by_doctor"] == 1
    assert c["unsure_R1"] == 2 and c["unsure_R2"] == 2 and c["unsure_both"] == 0
    hp = released(tmp, b)["patient-0001/note-001.txt"]
    assert "Kellerman" not in hp and "barbotage" in hp and "schoolteacher" in hp and "Fabry" in hp
    learn = json.loads(one(tmp, "learning_report-*.json").read_text())
    assert learn["unsure"] == {"reviewer_1": 2, "reviewer_2": 2, "both": 0,
                               "doctor_kept": 3, "doctor_removed": 1}
    raw = json.dumps(learn)
    for word in ("Kellerman", "barbotage", "schoolteacher", "Fabry", "Lindqvist", "smith_a") + tuple(REVIEWERS):
        assert word not in raw


def test_reviewer_marks_snap_to_whole_words(batch):
    tmp, bdir, b = batch
    t = _text(b, b["notes"][0])
    s = t.index("schoolteacher")
    r1 = decisions(b, "R1", add=[(0, s + 1, s + 9, "other")])          # "choolte" — half a word
    r2 = decisions(b, "R2", add=[(0, s - 8, s + 13, "other")])         # "retired schoolteacher"
    files = pair(tmp, b, r1, r2)
    dec = chert.load_decisions(b, files)
    n = b["notes"][0]
    items, finds = chert.note_items(n, dec["R1"]["notes"][n["id"]], dec["R2"]["notes"][n["id"]], t)
    (m,) = [i for i in items if i["source"] == "mark"]
    assert t[m["start"]:m["end"]] == "retired schoolteacher" and m["locked"]
    assert finds["both"] == 1
    release(tmp, b, bdir, files)
    hp = released(tmp, b)["patient-0001/note-001.txt"]
    assert "Fabry disease, [REDACTED], presents" in hp


def test_snap():
    text = "Seen by Dr. Kellerman. Call 415-555-0199 now, o'brien@example.org"
    assert chert.snap(text, 17, 19) == (12, 21)
    s = text.index("555")
    assert text[slice(*chert.snap(text, s, s + 1))] == "415-555-0199"
    s = text.index("brien")
    assert text[slice(*chert.snap(text, s, s + 2))] == "o'brien@example.org"
    assert chert.snap(text, 4, 5) is None                             # a space only


# ---------------------------------------------------------------- integrity

def test_keeping_an_identifier_the_tool_is_certain_of_is_an_integrity_violation(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b, decisions(b, "R1", keep={"4471822"}))
    page = chert.doctor([bdir], files, tmp / "doctor.html")
    data = _page_data(page["page"])
    assert data["batches"][0]["quality"]["violations"][0]["reason"] == "kept_certain_identifier"
    with pytest.raises(chert.ReviewError, match="needs a fresh review before release"):
        release(tmp, b, bdir, files)
    assert not released(tmp, b)
    alert = one(tmp, "integrity_alert-*.json")
    raw = alert.read_text()
    a = json.loads(raw)
    assert a["violations"] == [{"reason": "kept_certain_identifier", "reviewer_role": "reviewer 1", "count": 1}]
    for word in ("4471822", "Lindqvist", "smith_a") + tuple(REVIEWERS) + (DOCTOR,):
        assert word not in raw
    # The vote itself never keeps it either.
    dec = chert.load_decisions(b, files)
    n = b["notes"][0]
    items, _ = chert.note_items(n, dec["R1"]["notes"][n["id"]], dec["R2"]["notes"][n["id"]])
    mrn = [i for i in items if i["key"] == removal(b, 0, "4471822")["id"]][0]
    assert mrn["votes"]["R1"] == "remove" and mrn["tampered"] == ["R1"]


def test_a_file_changed_outside_its_page_is_an_integrity_violation(batch):
    tmp, bdir, b = batch
    good = write(tmp, "r1.json", decisions(b, "R1"))
    # 1. Sealed, then edited by hand (reading time raised).
    sealed = json.loads(write(tmp, "r2.json", decisions(b, "R2")).read_text())
    sealed["sealed_payload"] = sealed["sealed_payload"].replace('"seconds":30', '"seconds":300', 1)
    edited = tmp / "r2-edited.json"
    edited.write_text(json.dumps(sealed))
    # 2. Never sealed. 3. Sealed with the other reviewer's key.
    bare = write(tmp, "r2-bare.json", decisions(b, "R2"), seal=False)
    swapped = write(tmp, "r2-swapped.json", decisions(b, "R2"), key_for="R1")
    for bad in (edited, bare, swapped):
        q = chert.review_quality(b, chert.load_decisions(b, [good, bad]),
                                  {n["id"]: _text(b, n) for n in b["notes"]})
        assert [v["reason"] for v in q["violations"]] == ["file_mismatch"], bad.name
        with pytest.raises(chert.ReviewError, match="does not match the file its review page saved"):
            release(tmp, b, bdir, [good, bad])
    assert not released(tmp, b)


def test_reviews_faster_than_anyone_can_read_block_the_release(tmp_path):
    notes = {f"p{i}/n.txt": f"Visit {i}. Knee pain, improving on the current plan. Continue PT and "
                            f"recheck in six weeks. No fever, no swelling, gait steady.\n" for i in range(5)}
    d = _prepare(tmp_path, notes)[0]
    b = chert.load_batch(d)
    # One note of five skimmed: a gentle alert only.
    files = pair(tmp_path, b, r2=decisions(b, "R2", seconds={0: 1}))
    q = chert.review_quality(b, chert.load_decisions(b, files), {n["id"]: _text(b, n) for n in b["notes"]})
    assert not q["violations"] and any("less than the expected reading time" in a for a in q["alerts"])
    assert q["per_note"][b["notes"][0]["id"]]
    # Most notes confirmed in a second: a hard stop.
    fast = pair(tmp_path, b, r2=decisions(b, "R2", seconds=1), tag="-fast")
    with pytest.raises(chert.ReviewError, match="expected reading time"):
        release(tmp_path, b, d, fast)
    assert json.loads(one(tmp_path, "integrity_alert-*.json").read_text())["violations"][0]["reason"] == \
        "reading_time"
    assert chert.min_seconds("word " * 150) == 10 and chert.min_seconds("short") == 3


def test_the_doctors_file_is_checked(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b)
    # Edited outside the doctor's page.
    doc = doctor_file(tmp, [(b, files)])
    raw = json.loads(doc.read_text())
    raw["sealed_payload"] = raw["sealed_payload"].replace('"withhold":false', '"withhold":true', 1)
    doc.write_text(json.dumps(raw))
    with pytest.raises(chert.ReviewError, match="does not match the file the doctor's page saved"):
        chert.release(bdir, files, tmp / "released", doctor_file=doc)
    # Made against a different version of a reviewer's file.
    old = doctor_file(tmp, [(b, files)], fname="old.json")
    files2 = pair(tmp, b, r1=decisions(b, "R1", seconds=45), tag="-v2")
    with pytest.raises(chert.ReviewError, match="different version of reviewer 1"):
        chert.release(bdir, files2, tmp / "released", doctor_file=old)
    # A decision dated after the signature.
    late = doctor_file(tmp, [(b, files)], rulings={"Kellerman": "remove"},
                       decided_at="2026-09-26T03:00:00Z", fname="late.json")
    with pytest.raises(chert.ReviewError, match="after signing"):
        chert.release(bdir, files, tmp / "released", doctor_file=late)
    assert not released(tmp, b)


# ---------------------------------------------------------------- the doctor's page

def test_the_doctors_page_shows_everything_across_batches(tmp_path):
    notes = {**NOTES, "zz_third/a.txt": "CLINIC NOTE\nSeen by Dr. Kellerman. Doing well.\n"}
    dirs = _prepare(tmp_path, notes, batch_patients=2)
    assert len(dirs) == 2
    b1, b2 = (chert.load_batch(d) for d in dirs)
    f1 = pair(tmp_path, b1, r1=decisions(b1, "R1", keep={"Kellerman"}), tag="-1")
    f2 = pair(tmp_path, b2, tag="-2")
    page = chert.doctor(dirs, f1 + f2, tmp_path / "doctor.html")
    data = _page_data(page["page"])
    assert data["mode"] == "doctor" and len(data["batches"]) == 2 and page["unsure_bucket"] == 1
    assert [p["label"] for p in data["patients"]] == ["Batch 1 · Patient 1", "Batch 1 · Patient 2",
                                                     "Batch 2 · Patient 1"]
    assert data["seal"] == chert.page_key(b1["batch"] + "+" + b2["batch"], "DOC").hex()
    hp = next(n for n in data["notes"] if n["nid"] == b1["notes"][0]["id"])
    assert any("kept a word the tool read as a name" in a for a in hp["alerts"])
    assert "Kellerman" in hp["text"]                          # the doctor sees the notes themselves
    # One file from the doctor releases both batches together.
    doc = doctor_file(tmp_path, [(b1, f1), (b2, f2)])
    chert.release(dirs, f1 + f2, tmp_path / "released", doctor_file=doc)
    assert released(tmp_path, b1) and released(tmp_path, b2)
    assert list((tmp_path / "released").glob("accuracy_report-*.md"))


def test_one_doctor_page_covers_one_persons_batches(tmp_path):
    a = _prepare(tmp_path / "a", NOTES)[0]
    c = _prepare(tmp_path / "c", NOTES, authorized_by="Dr. Someone Else")[0]
    ba, bc = chert.load_batch(a), chert.load_batch(c)
    files = pair(tmp_path, ba, tag="a") + pair(tmp_path, bc, tag="c")
    with pytest.raises(chert.ReviewError, match="authorized by different people"):
        chert.doctor([a, c], files, tmp_path / "d.html")
    with pytest.raises(chert.ReviewError, match="given twice"):
        chert.doctor([a, a], files, tmp_path / "d.html")


def test_if_any_batch_fails_nothing_is_written(tmp_path):
    notes = {**NOTES, "zz_third/a.txt": "CLINIC NOTE\nDoing well.\n"}
    dirs = _prepare(tmp_path, notes, batch_patients=2)
    b1, b2 = (chert.load_batch(d) for d in dirs)
    f1 = pair(tmp_path, b1, tag="-1")
    f2 = pair(tmp_path, b2, tag="-2")
    doc = doctor_file(tmp_path, [(b1, f1)])                    # covers batch 1 only
    with pytest.raises(chert.ReviewError, match="does not cover batch 2"):
        chert.release(dirs, f1 + f2, tmp_path / "released", doctor_file=doc)
    assert not released(tmp_path, b1)


# ---------------------------------------------------------------- reports

def test_the_reports(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", add=[span(b, 0, "schoolteacher") + ("other",)])
    rep = release(tmp, b, bdir, pair(tmp, b, r1), add=[(0,) + span(b, 0, "chest pain") + ("other",)])
    acc = rep["accuracy"]
    assert acc["tool"]["precision"] == 100.0 and acc["doctor_marks"] == 1
    assert acc["reviewer_1"]["recall"] > acc["reviewer_2"]["recall"]
    md = one(tmp, "accuracy_report-*.md").read_text()
    assert "| The tool alone |" in md and "Reviewer 1" in md
    ev = one(tmp, "evidence_summary-001-*.md").read_text()
    for s in REVIEWERS + [DOCTOR, "Lindqvist", "Fabry", "smith_a", "schoolteacher"]:
        assert s not in ev and s not in md
    assert "3 notes from 2 patients" in ev
    local = one(tmp, "batch_report-001-*.md").read_text()
    assert "Jane Roe, RN" in local and "SHA-256" in local
    learn = json.loads(one(tmp, "learning_report-*.json").read_text())
    assert learn["tool_missed"] == {"OTHER_IDENTIFIER": 2}


def test_a_name_copied_to_other_notes_is_not_counted_as_an_independent_find(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", add=[span(b, 0, "schoolteacher") + ("other",)])
    r1["notes"][b["notes"][1]["id"]]["add"].append(
        {"start": 0, "end": 8, "type": "name", "propagated_from": b["notes"][0]["id"]})
    rep = release(tmp, b, bdir, pair(tmp, b, r1))
    assert rep["reviewer_finds"]["R1"] == 1 and rep["counts"]["propagated_marks"] == 1


def test_an_identifier_shape_left_after_review_holds_the_note(batch):
    tmp, bdir, b = batch
    both = {"4/16/2021"}               # a date the tool found by its shape, not certain
    rep = release(tmp, b, bdir, pair(tmp, b, decisions(b, "R1", keep=both), decisions(b, "R2", keep=both)))
    assert rep["counts"]["held_after_review"] == 1
    assert "patient-0001/note-001.txt" not in released(tmp, b)
    assert "held" in one(tmp, "release_map-batch-001-*.csv").read_text()


def test_a_source_note_changed_after_preparing_is_refused(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b)
    doc = doctor_file(tmp, [(b, files)])
    (Path(b["source_dir"]) / "zz_other/visit.txt").write_text("Changed.\n")
    with pytest.raises(chert.ReviewError, match="changed"):
        chert.release(bdir, files, tmp / "released", doctor_file=doc)


# ---------------------------------------------------------------- pieces

def test_capture_recapture():
    e = chert.capture_recapture(10, 8, 6)
    # Chapman: (11*9)/7 - 1 = 13.14 findable; 12 seen; about 1.1 missed.
    assert e["estimable"] and e["estimated_missed_by_both"] == 1.1 and e["found_by_either"] == 12
    assert not chert.capture_recapture(0, 0, 0)["estimable"]
    assert not chert.capture_recapture(1, 1, 1)["estimable"]      # too few shared finds


def test_merge_spans():
    m = chert.merge_spans([{"start": 0, "end": 4, "entity": "DATE_TIME", "layer": "regex"},
                            {"start": 2, "end": 9, "entity": "PERSON", "layer": "review"},
                            {"start": 12, "end": 15, "entity": "MRN", "layer": "uncertain"}])
    assert [(x["start"], x["end"], x["entity"]) for x in m] == [(0, 9, "PERSON"), (12, 15, "MRN")]
    assert m[0]["layer"] == "review" and m[1]["layer"] == "uncertain"
    mixed = chert.merge_spans([{"start": 0, "end": 4, "entity": "DATE_TIME", "layer": "review"},
                                {"start": 2, "end": 9, "entity": "PHONE_NUMBER", "layer": "regex"}])
    assert mixed[0]["entity"] == "OTHER_IDENTIFIER"


def test_lds_batches_show_what_the_profile_keeps(tmp_path):
    (tmp_path / "n" / "p").mkdir(parents=True)
    (tmp_path / "n" / "p" / "a.txt").write_text(
        "Pt Hubert Lindqvist from Marion, IA. Admitted March 14, 2024 for SBO.\n")
    d = chert.prepare(tmp_path / "n", tmp_path / "b", authorized_by="Dr. O", reviewers=REVIEWERS,
                       profile="lds")[0]
    b = chert.load_batch(d)
    assert b["profile"] == "limited_data_set"
    n = b["notes"][0]
    t = _text(b, n)
    kept = {t[k["start"]:k["end"]] for k in n["kept"]}
    assert any("March 14" in k for k in kept) and any("Marion" in k for k in kept)
    assert "Limited Data Set" in (d / "review-reviewer-1.html").read_text()


def test_preflight_refuses_every_review_file():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import preflight_check as pc
    for f in ("b/batch-001/batch.json", "b/batch-001/review-reviewer-1.html", "x/doctor.html",
              "decisions-ab12-R1.json", "doctor-1-ab12cd34.json", "out/release_map-batch-001.csv",
              "out/batch_report-001.md", "out/integrity_alert-001-ab.json",
              "out/accuracy_report-001.md", "out/learning_report-001.json",
              "out/evidence_summary-001-ab.md"):
        assert pc.ARTIFACT_FILES.search(f), f
    assert not pc.ARTIFACT_FILES.search("inkguard/chert.py")
    # The page TEMPLATE is code, not a filled page.
    assert not pc.ARTIFACT_FILES.search("inkguard/chert_page.html")


def test_safe_harbor_batches_list_nothing_as_kept(batch):
    tmp, bdir, b = batch
    assert all(not n["kept"] for n in b["notes"])


def test_placeholders_engine_dates_keep_the_year_reviewer_dates_do_not():
    eng = {"start": 0, "end": 10, "entity": "DATE_TIME", "layer": "regex"}
    rev = {**eng, "layer": "review"}
    assert chert.placeholder_for(eng, "03/14/1998") == "1998"
    assert chert.placeholder_for(rev, "03/14/1998") == "[DATE]"
    assert chert.placeholder_for({**rev, "entity": "OTHER_IDENTIFIER"}, "x") == "[REDACTED]"


# ---------------------------------------------------------------- the page itself (needs node)

NODE = shutil.which("node")


def _script():
    t = TEMPLATE.read_text()
    return t.split("<script>const DATA = __DATA__;</script>\n<script>", 1)[1].rsplit("</script>", 1)[0]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_script_parses(tmp_path):
    js = tmp_path / "page.js"
    js.write_text(_script())
    r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_pages_seal_matches_the_release(tmp_path):
    """The page's HMAC (plain JavaScript) and the release's (Python) agree,
    for ASCII, accents, other scripts, emoji and multi-block lengths."""
    src = _script()
    block = src[src.index("/* ------------------------------------------------------------ file seal */"):
                src.index("/* ------------------------------------------------------------ sign-off")]
    runner = tmp_path / "seal.js"
    runner.write_text("const DATA = {};\n" + block +
                      "\nconst c = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                      "\nconsole.log(JSON.stringify(c.map(x => hmacHex(x[0], x[1]))));")
    key = chert.page_key("abcd", "R1")
    cases = [[key.hex(), s] for s in ("", "a", "x" * 55, "x" * 56, "x" * 64, "é ñ 中文 😀 ’ —\n\t\"\\" * 40,
                                      json.dumps({"k": "Jane Roe, RN", "n": [1, 2]}))]
    r = subprocess.run([NODE, str(runner)], input=json.dumps(cases), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import hashlib
    import hmac
    assert json.loads(r.stdout) == [hmac.new(bytes.fromhex(k), s.encode(), hashlib.sha256).hexdigest()
                                    for k, s in cases]


# ------------------------------------ review findings, each one a test

def test_a_new_preparation_is_a_new_batch_and_old_decisions_do_not_fit(batch):
    tmp, bdir, b = batch
    old = pair(tmp, b)
    with pytest.raises(chert.ReviewError, match="empty folder"):
        chert.prepare(Path(b["source_dir"]), bdir.parent, authorized_by=DOCTOR, reviewers=REVIEWERS)
    again = chert.prepare(Path(b["source_dir"]), tmp / "again", authorized_by=DOCTOR,
                           reviewers=REVIEWERS, profile="lds")[0]
    assert chert.load_batch(again)["batch"] != b["batch"]
    with pytest.raises(chert.ReviewError, match="no decisions file"):
        chert.doctor([again], old, tmp / "d.html")


def test_merged_marks_never_leak_a_year_from_another_number(tmp_path):
    d = _prepare(tmp_path, {"p/a.txt": "Seen 3/5. Call 415-555-2019 with results.\n"})[0]
    b = chert.load_batch(d)
    r1 = decisions(b, "R1", add=[span(b, 0, "3/5. Call") + ("date",)])
    r2 = decisions(b, "R2", add=[span(b, 0, "Call 415") + ("other",)])
    release(tmp_path, b, d, pair(tmp_path, b, r1, r2))
    out = released(tmp_path, b)["patient-0001/note-001.txt"]
    assert "2019" not in out and "415" not in out and "3/5" not in out


def test_a_mark_cutting_into_an_age_takes_the_whole_age(tmp_path):
    d = _prepare(tmp_path, {"p/a.txt": "Pt is a 93-year-old woman with CHF.\n"})[0]
    b = chert.load_batch(d)
    r1 = decisions(b, "R1", add=[span(b, 0, "old woman") + ("other",)])
    release(tmp_path, b, d, pair(tmp_path, b, r1))
    out = released(tmp_path, b)["patient-0001/note-001.txt"]
    assert "93" not in out and "with CHF" in out


@pytest.mark.parametrize("damage", [
    lambda st: st["add"].append({"start": "abc", "end": 4, "type": "name"}),
    lambda st: st["add"].append({"start": 9, "end": 4, "type": "name"}),
    lambda st: st["add"].append({"start": 1, "end": 4, "type": "sneaky"}),
    lambda st: st.__setitem__("answers", ["remove"]),
    lambda st: st.__setitem__("answers", {"q0": "maybe"}),
    lambda st: st.__setitem__("keep", [["r1"]]),
    lambda st: st.__setitem__("unsure", "r1"),
    lambda st: (st.__setitem__("keep", ["r1"]), st.__setitem__("unsure", ["r1"])),
    lambda st: st.__setitem__("seconds", "x"),
    lambda st: st.__setitem__("seconds", -5),
])
def test_a_damaged_decisions_file_is_refused_before_anything_is_written(batch, damage):
    tmp, bdir, b = batch
    r2 = decisions(b, "R2")
    damage(r2["notes"][b["notes"][-1]["id"]])
    with pytest.raises(chert.ReviewError, match="malformed"):
        chert.load_decisions(b, pair(tmp, b, r2=r2))
    assert not released(tmp, b)


def test_a_batch_is_released_once(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b)
    doc = doctor_file(tmp, [(b, files)])
    chert.release(bdir, files, tmp / "released", doctor_file=doc)
    with pytest.raises(chert.ReviewError, match="released before"):
        chert.release(bdir, files, tmp / "released", doctor_file=doc)


def test_changes_after_signing_off_need_a_new_signature(batch):
    tmp, bdir, b = batch
    r2 = decisions(b, "R2")
    r2["notes"][b["notes"][0]["id"]]["confirmed_at"] = "2026-09-26T05:00:00Z"   # after signing
    with pytest.raises(chert.ReviewError, match="after signing off"):
        chert.doctor([bdir], pair(tmp, b, r2=r2), tmp / "d.html")


def test_a_batch_edited_after_preparing_is_refused(batch):
    tmp, bdir, b = batch
    raw = json.loads((bdir / "batch.json").read_text())
    assert raw["sealed"] is True                       # the test key is set
    raw["notes"][0]["removals"] = raw["notes"][0]["removals"][1:]
    (bdir / "batch.json").write_text(json.dumps(raw))
    with pytest.raises(chert.ReviewError, match="changed after it was prepared"):
        chert.load_batch(bdir)
    raw["profile"] = "limited_data_set"
    (bdir / "batch.json").write_text(json.dumps(raw))
    with pytest.raises(chert.ReviewError, match="changed after it was prepared"):
        chert.load_batch(bdir)


def test_a_signature_that_is_not_a_signature_is_refused(batch):
    tmp, bdir, b = batch
    r2 = decisions(b, "R2")
    r2["signed_off"] = "yes"
    with pytest.raises(chert.ReviewError, match="malformed"):
        chert.load_decisions(b, pair(tmp, b, r2=r2))


# ------------------------------------ review findings, second pass

def test_offsets_after_an_emoji_hit_the_right_word(tmp_path):
    text = "Msg from pt 😀😀😀😀: her nickname is Bo and she lives alone. Knee pain.\n"
    d = _prepare(tmp_path, {"p/a.txt": text})[0]
    b = chert.load_batch(d)
    t = _text(b, b["notes"][0])
    s = t.index("Bo ")
    u16 = lambda i: len(t[:i].encode("utf-16-le")) // 2   # noqa: E731  what the page counts
    assert chert.to_page_offset(t, s) == u16(s) == s + 4
    assert chert.from_page_offset(t, u16(s)) == s
    assert chert.from_page_offset(t, u16(t.index("😀")) + 1) == t.index("😀") + 1   # mid-character
    # The page's copy of the note carries page offsets; the batch keeps characters.
    page = _page_data(d / "review-reviewer-1.html")["notes"][0]
    for r_page, r_batch in zip(page["removals"], b["notes"][0]["removals"]):
        assert r_page["start"] == u16(r_batch["start"]) and r_page["end"] == u16(r_batch["end"])
    mark = [(0, u16(s), u16(s + 2), "name")]                                # as the page exports it
    release(tmp_path, b, d, pair(tmp_path, b, decisions(b, "R1", add=mark), decisions(b, "R2", add=mark)))
    out = released(tmp_path, b)["patient-0001/note-001.txt"]
    assert "Bo" not in out and " and she" in out


def test_a_batch_cannot_be_switched_to_unsealed(batch):
    tmp, bdir, b = batch
    raw = json.loads((bdir / "batch.json").read_text())
    raw["sealed"] = False
    (bdir / "batch.json").write_text(json.dumps(raw))
    with pytest.raises(chert.ReviewError, match="prepared without the clinic's key"):
        chert.load_batch(bdir)


def test_a_plain_file_cannot_carry_a_seal_of_its_own(batch):
    tmp, bdir, b = batch
    genuine = json.loads(write(tmp, "old.json", decisions(b, "R2")).read_text())
    forged = decisions(b, "R2", seconds=999)
    forged["_payload"], forged["_seal"] = genuine["sealed_payload"], genuine["seal"]
    with pytest.raises(chert.ReviewError, match="fields no review page writes"):
        chert.load_decisions(b, [write(tmp, "r1.json", decisions(b, "R1")),
                                  write(tmp, "f.json", forged, seal=False)])
    # Extra fields beside the page's wrapper are refused too.
    genuine["notes"] = {}
    with pytest.raises(chert.ReviewError, match="seal is damaged"):
        chert.load_decisions(b, [write(tmp, "r1.json", decisions(b, "R1")),
                                  write(tmp, "g.json", genuine, seal=False)])


def test_a_seal_that_is_not_hex_is_a_violation_not_a_crash(batch):
    tmp, bdir, b = batch
    w = json.loads(write(tmp, "r2.json", decisions(b, "R2")).read_text())
    w["seal"] = "é" * 64
    bad = tmp / "r2-bad.json"
    bad.write_text(json.dumps(w))
    with pytest.raises(chert.ReviewError, match="does not match the file its review page saved"):
        release(tmp, b, bdir, [write(tmp, "r1.json", decisions(b, "R1")), bad])
    assert one(tmp, "integrity_alert-*.json")


def test_doctor_decisions_are_tied_to_their_words(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b, decisions(b, "R1", add=[span(b, 0, "schoolteacher") + ("other",)]))
    dec = chert.load_decisions(b, files)
    n = b["notes"][0]
    t = _text(b, n)
    items, _ = chert.note_items(n, dec["R1"]["notes"][n["id"]], dec["R2"]["notes"][n["id"]], t)
    (m,) = [i for i in items if i["source"] == "mark"]
    assert m["key"] == f"m{t.index('schoolteacher')}-{t.index('schoolteacher') + len('schoolteacher')}"
    # A ruling for an item these reviews do not have is refused.
    with pytest.raises(chert.ReviewError, match="made against other files"):
        chert.final_spans(n, items, {"rulings": {"m0-3": "keep"}}, t)


def test_accented_names_work_end_to_end(tmp_path):
    src = tmp_path / "notes" / "p"
    src.mkdir(parents=True)
    (src / "a.txt").write_text("Seen by Dr. Kellerman. Doing well.\n", encoding="utf-8")
    d = chert.prepare(tmp_path / "notes", tmp_path / "b", authorized_by="Dra. Núñez",
                       reviewers=["José Ñúñez", "Zoë Brontë"])[0]
    b = chert.load_batch(d)
    files = pair(tmp_path, b)
    doc = doctor_file(tmp_path, [(b, files)], name="Dra. Núñez")
    chert.release(d, files, tmp_path / "released", doctor_file=doc)
    assert released(tmp_path, b)


def test_a_batch_is_released_once_anywhere(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b)
    doc = doctor_file(tmp, [(b, files)])
    with pytest.raises(chert.ReviewError, match="given twice"):
        chert.release([bdir, bdir], files, tmp / "released", doctor_file=doc)
    assert not (tmp / "released").exists() or not any((tmp / "released").iterdir())
    chert.release(bdir, files, tmp / "released", doctor_file=doc)
    assert json.loads((bdir / "released.json").read_text())["batch"] == b["batch"]
    with pytest.raises(chert.ReviewError, match="released before"):
        chert.release(bdir, files, tmp / "elsewhere", doctor_file=doc)
    # No staging folder is left behind, and combined reports carry the batch ids.
    assert not list((tmp / "released").glob(".staging-*"))
    (acc,) = list((tmp / "released").glob("accuracy_report-*.md"))
    assert acc.name.startswith("accuracy_report-001-") and len(acc.stem.split("-")[-1]) == 8


def test_the_evidence_counts_every_note_not_released(batch):
    tmp, bdir, b = batch
    release(tmp, b, bdir, pair(tmp, b), withhold={(0, 0), (0, 2)})
    ev = one(tmp, "evidence_summary-001-*.md").read_text()
    assert "1 note was released; 2 were not released (0 stopped by the software, 0 held by the " \
           "final check, 2 withheld by the clinician)" in ev
    assert "sealed by its page and verified at release" in ev
    assert "independently" not in ev


def test_a_reviewer_who_changes_nothing_earns_no_changes(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", keep={"Kellerman"}, add=[span(b, 0, "schoolteacher") + ("other",)])
    rep = release(tmp, b, bdir, pair(tmp, b, r1), rulings={"Kellerman": "remove"})
    a1, a2 = rep["accuracy"]["reviewer_1"], rep["accuracy"]["reviewer_2"]
    assert a1["changes"] == 2 and a1["changes_upheld"] == 50.0          # the mark upheld, the keep not
    assert a2["changes"] == 0 and a2["changes_upheld"] is None
    assert a2["precision"] == rep["accuracy"]["tool"]["precision"]       # their version is the tool's
    assert "Their own changes" in one(tmp, "accuracy_report-*.md").read_text()


def test_snapping_never_cuts_a_long_joined_token():
    token = "-".join(["ab12"] * 80)                                       # 399 characters, no spaces
    text = "Ref " + token + " seen."
    s = 4 + 300
    lo, hi = chert.snap(text, s, s + 2)
    assert (lo, hi) == (4, 4 + len(token))


# ------------------------------------ feedback for the maintainers

def test_the_report_for_the_software_maker(batch):
    tmp, bdir, b = batch
    r1 = decisions(b, "R1", keep={"Kellerman"}, flags={0: {"over": True}},
                   feedback={"rating": 4, "issues": ["over", "click"],
                             "text": "Removed Kellerman who is our attending, and the drag on Lindqvist didn't work."})
    r2 = decisions(b, "R2", feedback={"rating": 2, "issues": ["slow"], "text": "Too slow on the Rose Alvarado note."})
    files = pair(tmp, b, r1, r2)
    vendor = {"share": True, "rating": 5, "issues": ["under"],
              "text": "Very good overall. It missed the job title once.",
              "reviewers": {f"{b['batch']}:R2": {"include": False}}}
    doc = doctor_file(tmp, [(b, files)], rulings={"Kellerman": "keep"}, vendor=vendor,
                      flags={(0, 1): {"under": True, "page": True}},
                      add=[(0,) + span(b, 0, "chest pain") + ("other",)])
    rep = chert.release(bdir, files, tmp / "released", doctor_file=doc)
    assert rep["combined"]["maintainer_report"].startswith("send_to_maintainers-")
    vj = one(tmp, "send_to_maintainers-*.json")
    raw = vj.read_text()
    v = json.loads(raw)
    # Words from the notes never leave; people and dates neither.
    for s in ("Kellerman", "Lindqvist", "Rose", "Alvarado", "4471822", "chest pain", "smith_a") + tuple(REVIEWERS) + (DOCTOR,):
        assert s not in raw, s
    assert v["ratings"] == {"doctor": 5, "reviewer_1": [4], "reviewer_2": []}          # reviewer 2 left out
    assert v["issues"] == {"missed something": 1, "removed too much": 1,
                           "something did not respond to a click": 1}
    by = {c["from"]: c["text"] for c in v["comments"]}
    assert set(by) == {"doctor", "reviewer_1"}
    assert "[the word]" in by["reviewer_1"] and "drag" in by["reviewer_1"]
    assert by["doctor"].startswith("Very good overall")
    notes = {tuple(sorted(f["flags"])): f for f in v["flagged_notes"]}
    over = notes[("removed too much here",)]
    assert over["flagged_by"] == ["reviewer_1"] and over["tool_over_removed"] == {"PERSON/ner": 1}
    under = notes[("missed something here", "page problem here")]
    assert under["flagged_by"] == ["doctor"]
    assert v["accuracy"]["doctor_marks"] == 1 and "patient_text_check" in v and "contains_patient_text" not in v
    md = one(tmp, "send_to_maintainers-*.md").read_text()
    assert "Report for the Inkguard maintainers" in md and "Kellerman" not in md


def test_no_report_unless_the_doctor_shares_it(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b, decisions(b, "R1", feedback={"rating": 3, "issues": [], "text": "fine"}))
    rep = release(tmp, b, bdir, files, vendor={"share": False, "text": "private"})
    assert not list((tmp / "released").glob("send_to_maintainers-*"))


@pytest.mark.parametrize("fb", [{"rating": 9}, {"rating": True}, {"issues": ["hack"]}, {"text": 5},
                                {"text": "x" * 5000}, "yes"])
def test_malformed_feedback_is_refused(batch, fb):
    tmp, bdir, b = batch
    with pytest.raises(chert.ReviewError, match="malformed"):
        chert.load_decisions(b, pair(tmp, b, r2=decisions(b, "R2", feedback=fb)))
    with pytest.raises(chert.ReviewError, match="malformed"):
        chert.load_decisions(b, pair(tmp, b, r2=decisions(b, "R2", flags={0: {"over": "yes"}}), tag="-f"))


def test_integrity_messages_are_factual_and_short(batch):
    tmp, bdir, b = batch
    files = pair(tmp, b, decisions(b, "R1", keep={"4471822"}))
    with pytest.raises(chert.ReviewError) as e:
        release(tmp, b, bdir, files)
    msg = str(e.value)
    for word in ("fraud", "tamper", "cheat", "suspicious", "must", "wrongdoing"):
        assert word not in msg.lower()
    alert = json.loads(one(tmp, "integrity_alert-*.json").read_text())
    for word in ("fraud", "tamper", "real review", "wrongdoing"):
        assert word not in json.dumps(alert).lower()
