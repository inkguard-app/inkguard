# Module index

Every module in `inkguard/`, alphabetically. Module names are short
labels; this table says what each contains. A test checks that every module
is listed here. Modules marked *(in development)* belong to the review
workflow or the desktop app, which are not part of 0.25.0.

| Module | Contents |
|---|---|
| `_data` | Loading and writing the data files in `inkguard/data/` |
| `agate` | Final check that every word left in a note is accounted for |
| `amber` | Feedback report for the desktop app *(in development)*; its search patterns are also used by the 0.25.0 feedback check |
| `basalt` | Input normalisation (characters, spacing, look-alike letters) |
| `beryl` | Operation log (records operations, not text) |
| `calcite` | Reading and writing Word, Excel, PDF, CSV and text files |
| `chert` | Review workflow: preparation, reviewer pages, release *(in development)* |
| `cli` | Command-line tool |
| `cli_attest` | Local audit trail details (optional), the steps and the notice |
| `cli_docs` | The audit trail (PDF), citations (text) and feedback report (Word) |
| `cli_report` | Results folder layout, guide, audit trail, citations, feedback report |
| `cli_review` | Command-line list of words held for review |
| `clinical_context` | Patterns for measurements, scores and grades |
| `clinical_lexicon` | Clinical vocabulary used to keep clinical words |
| `clinical_translator` | Abbreviations that can be places or clinical terms |
| `config` | Settings |
| `contamination` | Detection of another patient's details in a note |
| `contract` | Interface between the engine and the rest of the program |
| `date_shift` | Optional date shifting |
| `deident_profile` | Safe Harbor and Limited Data Set profiles |
| `dolomite` | Tidy-up tool: reorders and renames files a team already holds as redacted *(in development)* |
| `fluorite` | About text, code fingerprint, Methods text, self-test |
| `garnet` | Scoring of name and place candidates |
| `granite` | Encrypted batch storage (desktop app) *(in development)* |
| `halite` | Settling a reviewed batch, export, review report *(in development)* |
| `hipaa` | Safe Harbor rules: dates, ZIP codes, ages |
| `input_classifier` | Recognising the shape of input (forms, tables, prose) |
| `jasper` | Names and places found from context (roles, relations, headers) |
| `lapis` | One-line provenance label on output files |
| `lexicon_care` | Care-setting vocabulary |
| `lexicon_data_v2` | Generated clinical vocabulary (data file) |
| `lexicon_english` | Generated English word list (data file) |
| `lexicon_gate` | Generated vocabulary for the final check (data file) |
| `lexicon_icd_vocab` | Generated ICD-10-CM vocabulary (data file) |
| `lexicon_neuro` | Neurosurgical vocabulary |
| `lexicon_open` | Generated open clinical vocabulary (data file) |
| `lexicon_rare` | Generated rare-disease names (data file) |
| `linkage` | Identification screening: rare conditions, small populations, events |
| `name_registry` | Generated list of names (data file) |
| `obsidian` | Re-check of output for identifiers that survived |
| `onyx` | Pseudonym codes |
| `opal` | Desktop app service: accounts, roles, security log, backups *(in development)* |
| `phi_scanner` | Detection layers |
| `policy` | Pipeline: runs the stages and decides each note's status |
| `pumice` | Reading all of one patient's notes together |
| `pyrite` | Practice batch and reviewer badge *(in development)* |
| `quartz` | Section map of a note |
| `rare_conditions` | Prevalence tiers for rare conditions |
| `security` | Key derivation |
| `shale` | Spreading a confirmed name to every occurrence |
| `talc` | Identifier types and placeholders |
| `topaz` | Support codes |
| `window` | Small window for the command-line tool (choose folders, run, open results) |
| `zircon` | Intake check of files before they are read |
