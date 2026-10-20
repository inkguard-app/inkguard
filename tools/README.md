# Vocabulary and name-guard builders

Every word list the engine ships is generated from openly licensed,
citable sources. Nothing is taken from textbooks or other copyrighted
teaching material. These builders are run by the maintainer, once per
release, and some of them download public source files; nothing in
`tools/` is installed with Inkguard.

```bash
# 1. Name guard: Faker name providers (MIT), 1990 and 2000 US Census
#    names (public domain), curated transliterations.
python tools/build_name_registry.py --census SRC_DIR

# 2. Open clinical vocabulary: Uberon, Disease Ontology, MONDO, MAxO,
#    NCI Thesaurus (OBO releases, from their GitHub repositories),
#    WordNet and ICD-10-CM (CMS).
python tools/build_open_lexicon.py --src SRC_DIR --icd icd10cm_codes.csv

# 3. ICD-10-CM description vocabulary. The second argument is the folder
#    with the census name files; without it the census part of the name
#    guard is skipped.
python tools/mine_icd_vocab.py icd10cm_codes.csv SRC_DIR

# 4. Ordinary English words that are nobody's name (WordNet 3.0).
python tools/build_english_words.py SRC_DIR

# 5. Abbreviations and drug and device brand words (NCI Thesaurus, MONDO,
#    openFDA, FDA GUDID).
python tools/build_gate_vocab.py SRC_DIR

# 6. Rare-disease names (MONDO).
python tools/build_rare_diseases.py SRC_DIR

```

Device and drug registry vocabulary (`inkguard/data/lexicon_data_v2.json.gz`)
is not rebuilt by a command here. Every term in it comes from FDA GUDID,
FDA drug listings or ICD-10-CM, and the source of each term is listed in
`tools/lexmine/lexicon_data_v2_sources.csv.gz`; that per-term list is the
record of where the vocabulary came from. The scripts in `tools/lexmine/`
are kept for the record; `mine_sources.py` and `integrate.py` do not run
as they stand, because they import a module that is not in this
repository.

`tools/build_care_vocab.py` builds a further vocabulary (MeSH, HL7 and
others) that was measured and not adopted (see
`inkguard/clinical_lexicon.py`); its output is not shipped.

Build (1) before (2): the open vocabulary refuses every token that is a
personal name in the registry or the full census list.

## The safety rule is not optional

A word that is anybody's name never becomes "always clinical". Eponyms
that are also surnames (Foley, Murphy, Wilson) count as clinical only
when a clinical noun follows ("Foley catheter", "Murphy sign"), and a
title in front ("Dr. Foley") overrides even that.

# Stress testing

Make a folder of invented notes of any size (from the synthetic benchmark),
optionally with awkward files added on purpose, and run the tool over it:

```bash
python tools/make_stress_set.py 1000 stress-1000 --nasty
python -m inkguard.cli stress-1000 -o stress-1000-clean
```

The tool should finish without crashing, account for every file in its
report, set aside what it cannot open safely, and release nothing it could
not check. Measured on a 2-core test machine: about one second per note.
