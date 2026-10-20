"""The intake guard. Every file is checked before the engine opens it: the
signature must match the extension, Office ZIPs must not be bombs or reach
outside themselves, and nothing that runs or launches may be opened. All
sample content is invented; none of it is real malware — the markers are the
shapes real documents are checked against."""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inkguard import zircon  # noqa: E402


def _docx(path: Path, extra: dict | None = None, replace: dict | None = None) -> Path:
    import docx
    d = docx.Document()
    d.add_paragraph("Patient seen in clinic. Plan: follow up.")
    d.save(path)
    if not extra and not replace:
        return path
    parts = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            parts[n] = z.read(n)
    for n, data in (replace or {}).items():
        parts[n] = data
    for n, data in (extra or {}).items():
        parts[n] = data
    with zipfile.ZipFile(path, "w") as z:
        for n, data in parts.items():
            z.writestr(n, data)
    return path


# ---------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------

def test_a_program_renamed_as_a_document_is_refused(tmp_path):
    p = tmp_path / "consult.pdf"
    p.write_bytes(b"MZ\x90\x00" + b"\x00" * 200)
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-EXEC"


def test_a_docx_that_is_not_a_zip_is_refused(tmp_path):
    p = tmp_path / "note.docx"
    p.write_bytes(b"not actually a zip file at all")
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-MISMATCH"


def test_a_pdf_that_is_also_a_zip_is_refused(tmp_path):
    p = tmp_path / "note.pdf"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 64)       # a ZIP wearing a .pdf name
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code in ("ZC-MISMATCH", "ZC-POLYGLOT")


def test_a_shortcut_named_as_a_pdf_is_refused(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"\x4c\x00\x00\x00\x01\x14\x02\x00" + b"\x00" * 64)
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-CONTAINER"


def test_an_ordinary_text_file_passes(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Seen today. Plan: rest.\n")
    assert zircon.inspect(p).ok


def test_a_text_file_with_binary_content_is_refused(tmp_path):
    p = tmp_path / "note.txt"
    p.write_bytes(b"Seen today\x00\x00\x01\x02binary")
    assert zircon.inspect(p).status == zircon.VERDICT_REFUSE


# ---------------------------------------------------------------------
# Office containers
# ---------------------------------------------------------------------

def test_an_ordinary_word_file_passes(tmp_path):
    assert zircon.inspect(_docx(tmp_path / "ok.docx")).ok


def test_a_zip_bomb_office_file_is_refused(tmp_path):
    p = tmp_path / "bomb.docx"
    # A highly compressible entry: a few kilobytes on disk, tens of megabytes
    # unpacked — the shape of a zip bomb.
    base = {}
    with zipfile.ZipFile(_docx(tmp_path / "seed.docx")) as z:
        for n in z.namelist():
            base[n] = z.read(n)
    base["word/huge.xml"] = b"A" * (60 * 1024 * 1024)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n, data in base.items():
            z.writestr(n, data)
    assert p.stat().st_size < 1_000_000           # small on disk
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-ZIP-BOMB"


def test_a_part_addressed_outside_the_document_is_refused(tmp_path):
    p = tmp_path / "trav.docx"
    _docx(p, extra={"../escape.xml": b"<x/>"})
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-ZIP-PATH"


def test_an_encrypted_office_part_is_refused(tmp_path):
    p = tmp_path / "enc.docx"
    _docx(p)
    # zipfile cannot write a real encrypted entry, so set the encryption bit
    # (bit 0 of the general-purpose flag) in the raw bytes, where it sits in
    # the local header (offset 6 after PK\x03\x04) and the central directory
    # header (offset 8 after PK\x01\x02), as a real encrypted file would.
    data = bytearray(p.read_bytes())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        if i != -1:
            data[i + off] |= 0x01
    p.write_bytes(bytes(data))
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-ZIP-ENCRYPTED"


# ---------------------------------------------------------------------
# Office content: refuse vs note
# ---------------------------------------------------------------------

def test_a_remote_template_is_refused(tmp_path):
    p = tmp_path / "remote.docx"
    rel = (b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
           b'2006/relationships"><Relationship Id="r1" TargetMode="External" '
           b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
           b'Target="http://example.invalid/evil.dotm"/></Relationships>')
    _docx(p, extra={"word/_rels/settings.xml.rels": rel})
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code in ("ZC-REMOTE", "ZC-REMOTE-TEMPLATE")


def test_an_xml_entity_declaration_is_refused(tmp_path):
    p = tmp_path / "xxe.docx"
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><root>&a;</root>'
    _docx(p, replace={"word/document.xml": bad})
    v = zircon.inspect(p)
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-XML-ENTITY"


def test_macros_are_noted_not_refused(tmp_path):
    p = tmp_path / "macro.docm"
    _docx(p, extra={"word/vbaProject.bin": b"\x00\x01fake macro bytes"})
    v = zircon.inspect(p)
    assert v.ok and any("macros" in n for n in v.notes)


# ---------------------------------------------------------------------
# PDF content
# ---------------------------------------------------------------------

def _pdf(path: Path, body: bytes = b"") -> Path:
    path.write_bytes(b"%PDF-1.5\n" + body + b"\n%%EOF\n")
    return path


def test_a_pdf_that_launches_a_program_is_refused(tmp_path):
    v = zircon.inspect(_pdf(tmp_path / "a.pdf", b"<< /S /Launch /F (calc.exe) >>"))
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-PDF-LAUNCH"


def test_a_pdf_with_an_attachment_is_refused(tmp_path):
    v = zircon.inspect(_pdf(tmp_path / "a.pdf", b"<< /Type /Filespec /EF << >> /EmbeddedFile 1 0 R >>"))
    assert v.status == zircon.VERDICT_REFUSE and v.code == "ZC-PDF-EMBED"


def test_a_pdf_with_page_scripts_is_noted_not_refused(tmp_path):
    v = zircon.inspect(_pdf(tmp_path / "a.pdf", b"<< /OpenAction << /S /JavaScript /JS (app.alert) >> >>"))
    assert v.ok and v.notes


def test_a_plain_pdf_passes(tmp_path):
    v = zircon.inspect(_pdf(tmp_path / "a.pdf", b"<< /Type /Catalog >>"))
    assert v.ok and v.notes == []


# ---------------------------------------------------------------------
# The sandbox keeps a crafted parser-killer from stopping the run.
# ---------------------------------------------------------------------

def _sleeper(_p):
    import time
    time.sleep(60)


def test_a_file_that_hangs_the_checker_is_set_aside_not_fatal(tmp_path):
    # The optional sandbox: a check that never returns is stopped on a timeout
    # and the file is set aside, rather than the run hanging.
    v = zircon._sandboxed(_sleeper, tmp_path / "x.pdf", timeout=2.0)
    assert v.status == zircon.VERDICT_REFUSE and v.code in ("ZC-TIMEOUT", "ZC-CRASH")


def test_the_guard_leaves_unreadable_kinds_to_the_classifier(tmp_path):
    # zircon only gates the types the engine reads; an image is not its call.
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0 jpeg")
    assert zircon.inspect(p).ok


# ---------------------------------------------------------------------
# Wired into the batch run.
# ---------------------------------------------------------------------

def test_the_batch_run_sets_aside_a_refused_file_and_logs_it(tmp_path):
    from inkguard import cli
    from inkguard.policy import evaluate
    inp = tmp_path / "in" / "p1"
    inp.mkdir(parents=True)
    (inp / "note.txt").write_text("Seen today. Plan: rest.\n")
    (inp / "consult.pdf").write_bytes(b"%PDF-1.5\n<< /S /Launch /F (calc.exe) >>\n%%EOF\n")
    stats = cli.RunStats()
    cli.process_directory(evaluate, tmp_path / "in", tmp_path / "out", stats, None, False, True, mode="mask")
    by = {Path(e.source).name: e for e in stats.files}
    assert by["consult.pdf"].outcome == "set_aside" and by["consult.pdf"].code == "ZC-PDF-LAUNCH"
    assert by["note.txt"].outcome == "deidentified"
    assert stats.accounting["verified"], stats.accounting["problems"]
    log = tmp_path / "out" / ".inkguard" / "intake_guard_log.md"
    assert log.exists()
    text = log.read_text()
    assert "ZC-PDF-LAUNCH" in text and "consult.pdf" not in text    # code yes, patient path no
