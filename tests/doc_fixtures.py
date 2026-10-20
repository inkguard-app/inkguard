"""Test documents that hide patient information in every place a real file
can: the body, runs split mid-name, tables, headers and footers, hidden
text, comments, tracked deletions, field codes, links, text boxes, images,
properties, template paths. All names and numbers are invented."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

PATIENT = "Hubert Lindqvist"
SECRETS = ("Lindqvist", "4471822", "123-45-6789", "Ellsworth", "Quinlan", "hubert.lindqvist",
           "Mercy Orchard", "hlindqvist", "Orchard Lane", "Rose Alvarado", "415-555-0199",
           "03/14/1951")


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def make_docx(path: Path) -> Path:
    import docx
    from docx.oxml import parse_xml
    from docx.shared import Pt
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    d = docx.Document()
    cp = d.core_properties
    cp.author = "Dr. Margaret Ellsworth"
    cp.last_modified_by = "jroe"
    cp.title = f"Consult for {PATIENT}"
    cp.comments = "draft for Mrs. Lindqvist"
    sec = d.sections[0]
    sec.header.paragraphs[0].text = f"Patient: {PATIENT}   MRN: 4471822"
    sec.footer.paragraphs[0].text = "Seen by Dr. Kellerman"
    p = d.add_paragraph("HPI: 72M with chest pain, retired schoolteacher. Patient ")
    p.add_run("Hub")
    p.add_run("ert Lindqvist").bold = True
    p.add_run(" reports improvement. Daughter Rose Alvarado at bedside.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Name", PATIENT
    t.cell(1, 0).text, t.cell(1, 1).text = "DOB", "03/14/1951"
    h = d.add_paragraph("Plan: follow up in clinic.")
    hidden = h.add_run(" Old address 12 Orchard Lane, Marion, IA 52302.")
    hidden.font.hidden = True
    c = d.add_paragraph("Tolerating diet. Ambulating well.")
    d.add_comment(c.runs, text=f"Check with Mrs. Lindqvist first", author="Nurse Pat Quinlan",
                  initials="PQ")
    # Tracked changes: a deletion that still holds an SSN, an insertion kept.
    tr = d.add_paragraph()
    tr._p.append(parse_xml(
        f'<w:del {W} w:id="91" w:author="Dr. Margaret Ellsworth" w:date="2021-03-02T10:00:00Z">'
        f'<w:r><w:delText>SSN 123-45-6789 on file. </w:delText></w:r></w:del>'))
    tr._p.append(parse_xml(
        f'<w:ins {W} w:id="92" w:author="jroe" w:date="2021-03-02T10:05:00Z">'
        f'<w:r><w:t>Return in 2 weeks.</w:t></w:r></w:ins>'))
    # A mail-merge field whose result is the name.
    f = d.add_paragraph("Addressed to: ")
    for xml in (f'<w:r {W}><w:fldChar w:fldCharType="begin"/></w:r>',
                f'<w:r {W}><w:instrText xml:space="preserve"> MERGEFIELD PatientName_Lindqvist </w:instrText></w:r>',
                f'<w:r {W}><w:fldChar w:fldCharType="separate"/></w:r>',
                f'<w:r {W}><w:t>{PATIENT}</w:t></w:r>',
                f'<w:r {W}><w:fldChar w:fldCharType="end"/></w:r>'):
        f._p.append(parse_xml(xml))
    # A link to the patient's email.
    lk = d.add_paragraph("Contact: ")
    rid = d.part.relate_to("mailto:hubert.lindqvist@example.com",
                           "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                           is_external=True)
    lk._p.append(parse_xml(
        f'<w:hyperlink {W} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        f'r:id="{rid}"><w:r><w:t>email on file</w:t></w:r></w:hyperlink>'))
    # A text box.
    tb = d.add_paragraph("See note: ")
    tb._p.append(parse_xml(
        f'<w:r {W} xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape id="tb1" '
        f'style="width:200pt;height:40pt"><v:textbox><w:txbxContent><w:p><w:r>'
        f'<w:t>Call Rose at 415-555-0199</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape>'
        f'</w:pict></w:r>'))
    # An image.
    d.add_picture(io.BytesIO(_png()))
    d.add_paragraph("Signed electronically.").runs[0].font.size = Pt(9)
    tmp = path.with_suffix(".tmp.docx")
    d.save(tmp)
    # Company and a template path with a user name, added to the package.
    with zipfile.ZipFile(tmp) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "docProps/app.xml":
                data = data.replace(b"</Properties>", b"<Company>Mercy Orchard Clinic</Company></Properties>")
            if info.filename == "word/settings.xml":
                data = data.replace(
                    b"<w:zoom", b'<w:attachedTemplate r:id="rIdT1"/><w:zoom', 1) \
                    if b"xmlns:r=" in data else data
            zout.writestr(info, data)
    tmp.unlink()
    return path


def make_xlsx(path: Path) -> Path:
    import datetime as dt
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = openpyxl.Workbook()
    wb.properties.creator = "Dr. Margaret Ellsworth"
    wb.properties.lastModifiedBy = "jroe"
    wb.properties.title = f"Registry export {PATIENT}"
    ws = wb.active
    ws.title = "Lindqvist labs"
    ws.append(["Patient", "MRN", "DOB", "Phone", "Glucose", "Notes"])
    ws.append([PATIENT, 4471822, dt.date(1951, 3, 14), "415-555-0199", 105,
               "Daughter Rose Alvarado called about results."])
    ws.append(["Marta Quispe", 5520913, dt.date(1933, 1, 2), "(415) 555-0142", 98,
               "Lives in Hickory, NC 28601."])
    ws.append(["Total", None, None, None, "=SUM(E2:E3)", None])
    ws["F2"].comment = Comment("Ask Mrs. Lindqvist about fasting", "Nurse Pat Quinlan")
    ws["A2"].hyperlink = "mailto:hubert.lindqvist@example.com"
    ws.row_dimensions[3].hidden = True
    ws.oddHeader.center.text = f"Registry — {PATIENT}"
    dv = DataValidation(type="list", formula1='"Dr. Kellerman,Dr. Ellsworth"')
    ws.add_data_validation(dv)
    dv.add("G2")
    hidden = wb.create_sheet("old")
    hidden.append(["SSN", "Name"])
    hidden.append(["123-45-6789", PATIENT])
    hidden.sheet_state = "hidden"
    wb.defined_names["PatientName"] = openpyxl.workbook.defined_name.DefinedName(
        "PatientName", attr_text='"Hubert Lindqvist"')
    wb.save(path)
    return path


def make_pdf(path: Path) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setAuthor("Dr. Margaret Ellsworth")
    c.setTitle(f"Discharge summary {PATIENT}")
    y = 740
    for line in (f"Patient: {PATIENT}   MRN: 4471822   DOB: 03/14/1951",
                 "Address: 12 Orchard Lane, Marion, IA 52302",
                 "HPI: 72M with chest pain, retired schoolteacher.",
                 "Daughter Rose Alvarado at bedside. Call 415-555-0199.",
                 "Plan: aspirin daily; follow up in clinic in 2 weeks.",
                 "Seen by Dr. Kellerman."):
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.drawString(72, 740, "Page 2: Tolerating diet. Ambulating well.")
    c.drawString(72, 722, f"Addendum for {PATIENT} reviewed.")
    c.save()
    return path
