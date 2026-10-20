# Third-party data and licences

The engine ships word lists generated from the sources below. Every
entry in the open clinical vocabulary (`inkguard/data/lexicon_open.json.gz`,
loaded by `inkguard/lexicon_open.py`) names the source or sources that attest
it; `compound` marks a two-word term built from attested words. `inkguard/data/lexicon_data_v2.json.gz` (22,665 device, drug and
diagnosis terms) comes entirely from FDA GUDID, FDA drug listings and
ICD-10-CM, all public domain; each term's source is listed in
`tools/lexmine/lexicon_data_v2_sources.csv.gz`. No word list is taken from textbooks, review books or other
copyrighted teaching material.

These notes record provenance. They are not legal advice.

## Clinical vocabulary

| Source | Content used | Licence | Citation |
|---|---|---|---|
| [Uberon](https://obofoundry.org/ontology/uberon.html) (release 2026-06-19) | Anatomy term names, synonyms, definitions | CC BY 3.0 | Mungall CJ et al. Uberon, an integrative multi-species anatomy ontology. *Genome Biol.* 2012;13:R5 |
| [Human Disease Ontology](https://disease-ontology.org) (release 2026-08-31) | Disease names, synonyms, definitions | CC0 1.0 | Schriml LM et al. The Human Disease Ontology 2022 update. *Nucleic Acids Res.* 2022;50:D1255–61 |
| [MONDO](https://mondo.monarchinitiative.org) (release 2026-09-01) | Disease names, synonyms, definitions | CC BY 4.0 | Vasilevsky NA et al. Mondo: unifying diseases for the world, by the world. *medRxiv* 2022 |
| [MAxO](https://github.com/monarch-initiative/MAxO) (release 2026-01-15) | Medical action and procedure names | CC BY 4.0 | Carmody LC et al. The Medical Action Ontology. *Med* 2023 |
| [NCI Thesaurus, OBO edition](https://obofoundry.org/ontology/ncit.html) (24.04e) | Anatomy, disease and finding, pharmacologic substance, procedure, device, microorganism and abnormal-cell branches only. The geographic branch is excluded, and its words are blocked. | CC BY 4.0 | Sioutos N et al. NCI Thesaurus. *J Biomed Inform.* 2007;40:30–43 |
| ICD-10-CM (CMS / NCHS) | Code description vocabulary, chapters A–T | Public domain | |
| FDA Global Unique Device Identification Database (GUDID) | Device and brand names | Public domain | |
| FDA drug listings | Drug names | Public domain | |
| [WordNet 3.0](https://wordnet.princeton.edu) (Princeton University) | Medical lexicographer files (clinical vocabulary); all lowercase lemmas and regular past tenses, minus every name we hold (`inkguard/lexicon_english.py`, used only to recognise ordinary words) | WordNet 3.0 licence (permissive, notice required; reproduced below) | Miller GA. WordNet: a lexical database for English. *Commun ACM.* 1995;38(11):39–41 |

**Changes made:** term names, synonyms and definitions were split into
single words. Words that are personal names, common English words or
geographic names were removed, as described in
`tools/build_open_lexicon.py`. Eponyms were separated so that they count
as clinical only when a clinical noun follows them. No ontology structure
or definition text is redistributed.

**Licences and attribution:** Uberon is used under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/); MONDO, MAxO, the
NCI Thesaurus, the Cell Ontology, GeoNames and the Meta-Inventory under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); the Human Disease
Ontology under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
For every source in this file, terms were split into single words, filtered
and re-sorted as described above; no definitions or structure are kept. No
endorsement by any licensor is implied.

**Deliberately not used:**

- Human Phenotype Ontology: its licence has custom terms whose commercial
  conditions we could not confirm.
- UMLS and SNOMED CT: both require a licence.
- Any textbook.

**Curated by this project:** about 365 entries are our own curation and
are not attested by the sources above. They are:

- standard clinical abbreviations (CBC, BID, NPO…);
- dosing frequencies;
- a short list of operating-room consumable brand names (Monocryl,
  Prolene, Mastisol…);
- the hand-reviewed eponym ambiguity lists;
- chart shorthand (postop, dispo, PACU, Tdap…);
- the workplace and facility kind words and provider-role cues in
  `inkguard/jasper.py`.

The list can be regenerated with the check in `tools/build_open_lexicon.py`.

## agate vocabulary (`inkguard/lexicon_gate.py`, gate-only)

| Source | Content used | Licence |
|---|---|---|
| NCI Thesaurus (clinical branches) | Synonyms with term type AB (abbreviations); two-word term pairs | CC BY 4.0 |
| MONDO | Synonyms typed ABBREVIATION | CC BY 4.0 |
| Uberon | Two-word term pairs | CC BY 3.0 |
| openFDA Drugs@FDA and NDC | Words of brand names | Public domain |
| FDA GUDID | Words of device brand names occurring in 3+ brands | Public domain |
| [GeoNames](https://www.geonames.org) via [geonamescache](https://github.com/yaph/geonamescache) | Names of cities, towns and US counties, used only to hold a note for review | Data CC BY 4.0; package MIT (build time only) |
| NCI Thesaurus, **all** top-level branches except Conceptual Entity (organisations, places) and Retired; the geographic branch excluded | Single words (4+ letters) of every term and synonym, minus every name and place we hold (`EXTRA_VOCAB`); words the thesaurus itself uses as surnames kept apart (`EXTRA_EPONYMS`) | CC BY 4.0 |
| [Experimental Factor Ontology](https://www.ebi.ac.uk/efo/) (EFO v3.94.0) | Same use as the row above | Apache 2.0 |
| [Cell Ontology](https://obofoundry.org/ontology/cl.html) (CL, release 2026-06-08) | Same use as the row above | CC BY 4.0 |
| [Medical Abbreviation and Acronym Meta-Inventory](https://github.com/lisavirginia/clinical-abbreviations) v1.0.0 (Grossman Liu L et al. *Sci Data.* 2021;8:149) | Short forms only (no expansions): those attested in a clinical-note source (Vanderbilt clinic notes and discharge summaries, Stetson, Columbia, Berman), two-letter forms only when two such sources agree; from UMLS LRABR and ADAM only forms of four or more characters. Records whose only source is Wikipedia (CC BY-SA) are not used. | CC BY 4.0 (the compilation). Contains short forms derived from the NLM SPECIALIST Lexicon (LRABR), in the release included in the Meta-Inventory v1.0.0, and from ADAM (Zhou W, Torvik VI, Smalheiser NR. *Bioinformatics.* 2006;22:2813–8). See the SPECIALIST note below. |
| FDA GUDID device brand names | Instrument eponyms: a surname standing before an instrument word ("Kerrison rongeur") in 2+ brands, excluding the 5,000 most common US surnames and every first name (`DEVICE_EPONYMS`) | Public domain |

## Two-reviewer flags (`inkguard/lexicon_rare.py`, review workflow only, in development)

| Source | Content used | Licence |
|---|---|---|
| MONDO (release 2026-09-01), classes in the `orphanet_rare` subset and at least one more rare-disease subset (GARD, NORD, DOID, NCIt, MONDO curated), groupings excluded | Rare-disease names and exact synonyms, used only to flag a note so both reviewers check the combination | CC BY 4.0 |

## Name guard

| Source | Licence |
|---|---|
| [Faker](https://github.com/joke2k/faker) name providers, all locales | MIT |
| 1990 US Census surname and first-name files (via [treyhunner/names](https://github.com/treyhunner/names)) | Public domain |
| [anyascii](https://github.com/anyascii/anyascii), used at build time for transliteration only | ISC |

## Runtime dependencies

| Package | Licence | Note |
|---|---|---|
| spaCy, `en_core_web_lg` | MIT | |
| Microsoft Presidio | MIT | |
| wordfreq | Code Apache-2.0. Data CC BY-SA 4.0 (Robyn Speer) | Installed as a dependency, not redistributed by this project. Inkguard uses word frequencies only as a filter; no wordfreq data is copied into its files. |
| cryptography | Apache-2.0 / BSD | |
| keyring (and its platform helpers: pywin32-ctypes, jaraco.*, importlib_metadata; SecretStorage and jeepney on Linux) | MIT (helpers BSD-3 / MIT / Apache-2.0) | Desktop app only (in development). Holds the app's tamper counter and its key material (this computer's half of the records key, the work key and each batch key) in the operating system's protected store; no patient data or passwords. |
| python-docx | MIT | Reads and writes Word files; writes the feedback report. |
| openpyxl | MIT | Reads and writes Excel files. |
| pdfplumber | MIT | Reads text-based PDF files. |
| pypdfium2 (PDFium) | Apache-2.0 / BSD-3-Clause | Renders PDF pages. |
| pypdf | BSD-3-Clause | Reads and checks PDF files. |
| Pillow | MIT-CMU (HPND) | Images of PDF pages. |
| pytesseract | Apache-2.0 | Page check; calls Tesseract (Apache-2.0) if installed. |
| ReportLab | BSD-3-Clause | Writes the audit trail (PDF). |

## Benchmarks

| Item | Licence | Note |
|---|---|---|
| `benchmark/` and `benchmark/neuro/` (the messy-notes benchmark and the clinical-preservation set) | CC BY 4.0 | Written for this project; all content invented. Clinical substance for the messy-notes patients was generated with [Synthea](https://github.com/synthetichealth/synthea) (Apache-2.0). |
| [ASQ-PHI](https://doi.org/10.1016/j.dib.2026.112586) (Weatherhead J; [Mendeley Data](https://data.mendeley.com/datasets/csz5dzp7nx/1)) | Not stated on the dataset page | Test set only (`tools/eval_asq_phi.py`); not distributed here. |
| ACI-Bench (Yim et al., *Sci Data* 2023) | CC BY 4.0 | Test set only (`tools/eval_external.py`); not distributed here. |
| Microsoft presidio-research synthetic data | MIT | Test set only; not distributed here. |
| Taiwan synthetic clinical notes with PHI labels (fireman333) | CC BY 4.0 | Test set only; not distributed here. |
| Faker | MIT | Used by `tools/make_mixed_set.py` to generate test notes. |
| Microsoft Presidio analyzer, default configuration | MIT | Used in `tools/eval_preservation.py --baseline` only, as a reference point. It is also a runtime dependency (above). No endorsement by Microsoft is implied. |

## The desktop app (`desktop/`, in development)

| Package | Licence | Note |
|---|---|---|
| Tauri 2 (`tauri`, `tauri-build`, `tauri-plugin-dialog`, `tauri-plugin-single-instance`) and their Rust dependencies | MIT / Apache-2.0 | The window, the operating system's webview and the file picker. |
| qrcode-generator by Kazuhiko Arase (`desktop/ui/js/vendor/qrcode.js`) | MIT | Draws the phone-linking QR code as shapes on screen. Copied in unchanged; its licence header is kept in the file. |
| PyInstaller (build machines only) | GPL-2.0 with an exception for the programs it produces | Packages the desktop service as a program. Its licence exception lets the programs it builds be distributed under their own licence (here Apache-2.0); PyInstaller itself is not distributed. |

No web frameworks or style libraries are used: the screens are plain HTML,
CSS and JavaScript written for this app.

## WordNet 3.0 licence (reproduced as required)

```
WordNet Release 3.0

This software and database is being provided to you, the LICENSEE, by  
Princeton University under the following license.  By obtaining, using  
and/or copying this software and database, you agree that you have  
read, understood, and will comply with these terms and conditions.:  
  
Permission to use, copy, modify and distribute this software and  
database and its documentation for any purpose and without fee or  
royalty is hereby granted, provided that you agree to comply with  
the following copyright notice and statements, including the disclaimer,  
and that the same appear on ALL copies of the software, database and  
documentation, including modifications that you make for internal  
use or for distribution.  
  
WordNet 3.0 Copyright 2006 by Princeton University.  All rights reserved.  
  
THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON  
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR  
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON  
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-  
ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE  
OF THE LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT  
INFRINGE ANY THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR  
OTHER RIGHTS.  
  
The name of Princeton University or Princeton may not be used in  
advertising or publicity pertaining to distribution of the software  
and/or database.  Title to copyright in this software, database and  
any associated documentation shall at all times remain with  
Princeton University and LICENSEE agrees to preserve same.
```

## NLM SPECIALIST Lexicon (LRABR), via the Meta-Inventory

Some abbreviation short forms in `inkguard/lexicon_gate.py` derive from the
SPECIALIST Lexicon's LRABR file, a United States Government product of the
National Library of Medicine, in the release included in the Medical
Abbreviation and Acronym Meta-Inventory v1.0.0. **Modifications:** expansions
removed; only short forms of four or more characters kept; filtered against
the names and places this project holds. Redistributed under the
[SPECIALIST NLP Tools Terms and Conditions](https://lhncbc.nlm.nih.gov/LSG/Docs/termsAndConditions.html),
which require, among other things, that redistributions include those
conditions, state the release and any modifications, claim no proprietary
rights, and not use the names of NLM, NIH or HHS to endorse or promote
derived products. No endorsement by NLM is implied.

## Faker — name data (inkguard/data/name_registry.json.gz)

Personal names in the name guard come in part from Faker's name providers.

```
Copyright (c) 2012 Daniele Faraglia

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
