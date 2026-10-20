"""Patient-file mode: read every note of a
patient first, then redact each note with what the whole file taught."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

from inkguard import pumice  # noqa: E402
from inkguard.policy import evaluate  # noqa: E402

FILE = [
    "ADMISSION H&P\nPatient: Hubert Lindqvist   MRN: 4471822\n"
    "HPI: 71M s/p prior Pfannenstiel incision, now SBO.\n"
    "Social: retired baker, works part-time for Supico Bakery. Daughter Rose Alvarado at bedside.",
    "PROGRESS NOTE POD2\nRose visited, upset about discharge timing. Pt asks if he can be back "
    "at Supico next month.\nPfannenstiel well healed. Foley out. BP rose to 150 overnight.",
    "PORTAL MESSAGE\nhi this is rose, hubert's daughter. can he shower yet?\nthanks",
]


def test_the_whole_file_is_read_before_any_note_is_redacted():
    results, ctx = pumice.process(FILE, patient_key="p1", redaction_mode="mask")
    assert all(r.action.value != "blocked" for r in results)
    admission, progress, portal = (r.clean_text for r in results)
    for out in (admission, progress, portal):
        for name in ("Hubert", "Lindqvist", "Alvarado"):
            assert name not in out and name.lower() not in out
    assert "Rose" not in progress and "rose," not in portal     # the daughter, everywhere
    assert "BP rose to 150" in progress                        # the verb stays
    assert "[EMPLOYER]" in progress                            # "back at Supico"
    assert "Pfannenstiel well healed" in progress              # the file explains it
    assert ctx.summary()["names_confirmed"] >= 2


def test_a_word_the_file_uses_for_a_person_is_never_clinical_for_the_file():
    ctx = pumice.learn([
        "Seen by Dr. Pfannenstiel today.",
        "Pfannenstiel incision healing well.",
    ])
    assert "pfannenstiel" not in ctx.understood


def test_one_patients_file_never_reaches_another():
    a = pumice.learn(FILE, patient_key="a")
    b = pumice.learn(["Patient: Marta Quispe. Pain 3/10."], patient_key="b")
    assert not ({n.lower() for n in a.names} & {n.lower() for n in b.names})
    # Without A's file, B's note knows nothing of "Supico".
    r = evaluate("Plan: follow up with Quispe clinic.", file_context=b)
    assert "Supico" not in (b.places or {})


def test_the_review_list_separates_the_tools_gap_from_the_writing():
    from inkguard import cli
    md = cli.review_list("p9", [{"file": "p9/a.txt", "words_to_check": [
        {"word": "Bookwalter", "reason": "unknown_word", "holds_the_note": True},
        {"word": "Hubert", "reason": "name_left_in", "holds_the_note": True},
        {"word": "VCUG", "reason": "unknown_acronym", "holds_the_note": True}]}])
    assert "Words the tool is still learning" in md
    assert "Writing the tool could not interpret" in md
    assert "Could be either" in md


def test_cli_reads_the_file_and_writes_the_review_list(tmp_path):
    from inkguard import cli
    notes = tmp_path / "in" / "p1"
    notes.mkdir(parents=True)
    for i, t in enumerate(FILE + ["PROGRESS\nKUB and VCUG ok. Tolerating diet. Asked again about barbotage."]):
        (notes / f"n{i}.txt").write_text(t)
    stats = cli.RunStats()
    cli.process_directory(evaluate, tmp_path / "in", tmp_path / "out", stats, None, False, True,
                          mode="mask")
    assert stats.file_mode and stats.files_read == 1
    progress = cli.done_path(tmp_path / "out", "p1/n1.txt").read_text()
    assert "Rose" not in progress and "[EMPLOYER]" in progress
    rl = tmp_path / "out" / cli.PILE_FLAGGED / "p1" / cli.WORDS_FILE
    # VCUG is now vocabulary (the abbreviation Meta-Inventory); the
    # procedure word is not yet, so it is what the review list asks about.
    assert rl.exists() and "barbotage" in rl.read_text() and "VCUG" not in rl.read_text()
    assert rl.read_text().startswith("# Words to check")


def test_note_by_note_is_still_available(tmp_path):
    from inkguard import cli
    notes = tmp_path / "in" / "p1"
    notes.mkdir(parents=True)
    (notes / "a.txt").write_text(FILE[0])
    stats = cli.RunStats()
    cli.process_directory(evaluate, tmp_path / "in", tmp_path / "out", stats, None, False, True,
                          mode="mask", file_mode=False)
    assert not stats.file_mode and stats.files_read == 0
