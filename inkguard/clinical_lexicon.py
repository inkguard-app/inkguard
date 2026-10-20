"""
Clinical lexicon — the "never redact this" list.

WHY THIS FILE EXISTS
--------------------
Generic NER models are trained on news and web text. In that world,
"Parkinson", "Crohn", "Wernicke", "Osler" and "Graves" are surnames.
In clinical text they are DIAGNOSES. Without a clinical vocabulary,
a redactor that relies on such a model turns

    "Guillain-Barre with albuminocytologic dissociation on LP"
into
    "<PERSON> with albuminocytologic dissociation on LP"

...and whoever reads the note next is reasoning about an unnamed disease. That is
not a cosmetic problem. Over-redaction of clinical terms is a PATIENT
SAFETY failure: it silently destroys the clinical meaning of the note
while the tool reports "success."

So: PHI detection is *subtractive* against a clinical vocabulary.
A span that matches a known medical concept is kept, even when the NER
model reads it as a person's name, unless the word is also a personal
name: such words count as clinical only beside a clinical word
("Wilson disease"). The weaker vocabulary tier (is_vocabulary_word)
also gives way to a name title such as "Dr." before the word.

MAINTENANCE NOTE
----------------
This list is deliberately explicit and auditable rather than a fuzzy
model. A compliance reviewer can read it. Add to it whenever a user
reports an over-redaction.
"""

# ---------------------------------------------------------------------
# Eponymous diseases, signs, syndromes, criteria, and anatomy.
# These are the highest-risk false positives — NER reads them as PERSON.
# ---------------------------------------------------------------------
EPONYMS: set[str] = {
    # Neuro / psych
    "alzheimer", "alzheimers", "parkinson", "parkinsons", "huntington",
    "wernicke", "korsakoff", "wernicke-korsakoff", "broca", "babinski",
    "guillain", "barre", "guillain-barre", "bell", "bells", "tourette",
    "asperger", "aspergers", "kluver", "bucy", "kluver-bucy", "lewy",
    "creutzfeldt", "jakob", "creutzfeldt-jakob", "charcot", "horner",
    "brown-sequard", "kernig", "brudzinski", "romberg", "lhermitte",
    "todd", "wallenberg", "weber", "millard-gubler", "foster-kennedy",
    "munchausen", "ganser", "capgras", "cotard", "othello", "ekbom",
    # Cardio
    "osler", "janeway", "roth", "duke", "jones", "kussmaul", "levine",
    "austin-flint", "graham-steell", "de-musset", "quincke", "corrigan",
    "beck", "ewart", "virchow", "homans", "wolff", "parkinson-white",
    "wolff-parkinson-white", "brugada", "torsades", "eisenmenger",
    "tetralogy", "fallot", "ebstein", "marfan", "takotsubo", "prinzmetal",
    "dressler", "mobitz", "wenckebach", "stokes", "adams", "stokes-adams",
    # Endocrine
    "cushing", "cushings", "addison", "addisons", "graves", "hashimoto",
    "sheehan", "sheehans", "conn", "conns", "somogyi", "dawn",
    "waterhouse", "friderichsen", "waterhouse-friderichsen", "nelson",
    "de-quervain", "riedel", "plummer", "zollinger", "ellison",
    "zollinger-ellison", "whipple", "carney", "sipple", "wermer",
    # GI / hepatic
    "crohn", "crohns", "barrett", "barretts", "mallory", "weiss",
    "mallory-weiss", "boerhaave", "murphy", "mcburney", "rovsing",
    "psoas", "obturator", "cullen", "grey-turner", "courvoisier",
    "hirschsprung", "meckel", "ogilvie", "budd", "chiari", "budd-chiari",
    "wilson", "wilsons", "gilbert", "crigler", "najjar", "crigler-najjar",
    "dubin", "johnson", "dubin-johnson", "rotor", "peutz", "jeghers",
    "peutz-jeghers", "lynch", "gardner", "charcot-triad", "reynolds",
    # Renal
    "goodpasture", "berger", "bergers", "alport", "fanconi", "bartter",
    "gitelman", "liddle", "wegener", "churg", "strauss", "churg-strauss",
    "henoch", "schonlein", "henoch-schonlein", "kimmelstiel", "wilson",
    "ask-upmark", "potter",
    # Heme / onc
    "hodgkin", "hodgkins", "burkitt", "reed", "sternberg",
    "reed-sternberg", "auer", "philadelphia", "bence", "jones",
    "bence-jones", "von-willebrand", "willebrand", "christmas",
    "glanzmann", "bernard", "soulier", "bernard-soulier", "evans",
    "diamond", "blackfan", "fanconi-anemia", "waldenstrom", "richter",
    "howell", "jolly", "howell-jolly", "heinz", "pappenheimer",
    "rouleaux", "schilling", "coombs", "donath", "landsteiner",
    # Rheum / MSK
    "sjogren", "sjogrens", "raynaud", "raynauds", "reiter", "behcet",
    "takayasu", "kawasaki", "still", "stills", "felty", "caplan",
    "paget", "pagets", "dupuytren", "osgood", "schlatter",
    "osgood-schlatter", "legg", "calve", "perthes", "scheuermann",
    "colles", "smith", "jones-fracture", "bouchard", "heberden",
    "baker", "morton", "tinel", "phalen", "finkelstein", "trendelenburg",
    "ehlers", "danlos", "ehlers-danlos", "gower", "gowers",
    # Pulm / ID
    "pancoast", "curschmann", "charcot-leyden", "ghon", "ranke",
    "mantoux", "pott", "potts", "lofgren", "hamman", "rich",
    "legionella", "pneumocystis", "epstein", "barr", "epstein-barr",
    "koplik", "nikolsky", "negri", "guarnieri", "donovan", "ziehl",
    "neelsen", "ziehl-neelsen", "gram", "wright", "giemsa",
    # Derm
    "stevens", "johnson", "stevens-johnson", "lyell", "sweet",
    "darier", "wickham", "auspitz", "koebner", "pautrier", "sezary",
    # Genetics / peds
    "down", "downs", "turner", "klinefelter", "edwards", "patau",
    "prader", "willi", "prader-willi", "angelman", "rett", "williams",
    "digeorge", "noonan", "marfan", "apgar", "tanner", "barlow",
    "ortolani", "erb", "klumpke", "duchenne", "becker", "wilms",
    "ewing", "langerhans", "kasabach", "merritt", "hirschsprung",
    # Obstetrics
    "braxton", "hicks", "braxton-hicks", "chadwick", "goodell", "hegar",
    "naegele", "montgomery", "couvelaire", "bishop",
    # Ophtho / ENT
    "argyll", "robertson", "argyll-robertson", "marcus", "gunn",
    "marcus-gunn", "adie", "hutchinson", "rinne", "weber-test",
    "meniere", "menieres", "bell-palsy", "ramsay", "hunt", "ramsay-hunt",
    "eustachian", "schwannoma",
}

# ---------------------------------------------------------------------
# Clinical scales, scores, criteria, and instruments. Also frequently
# eponymous ("Hamilton", "Glasgow", "Beck", "Wells", "Ranson").
# ---------------------------------------------------------------------
CLINICAL_SCALES: set[str] = {
    "glasgow", "hamilton", "beck", "wells", "ranson", "apache", "curb",
    "curb-65", "psi", "chads", "cha2ds2-vasc", "has-bled", "timi",
    "grace", "meld", "child-pugh", "pugh", "nihss", "rankin", "asia",
    "mmse", "moca", "phq", "phq-9", "gad-7", "audit", "cage", "ciwa",
    "cows", "panss", "ymrs", "madrs", "bprs", "aims", "morse", "braden",
    "norton", "karnofsky", "ecog", "asa", "mallampati", "centor",
    "alvarado", "geneva", "perc", "sofa", "qsofa", "sirs", "apgar",
    "bishop", "framingham", "duke-criteria", "light", "lights",
    "ottawa", "nexus", "canadian", "san-francisco", "heart-score",
}

# ---------------------------------------------------------------------
# Drug names that NER commonly mistakes for names/places. Not exhaustive
# by design — the regex heuristics below catch most generic drug stems.
# ---------------------------------------------------------------------
DRUG_NAMES: set[str] = {
    "adderall", "ritalin", "concerta", "vyvanse", "strattera",
    "prozac", "zoloft", "paxil", "lexapro", "celexa", "effexor",
    "cymbalta", "wellbutrin", "remeron", "buspar", "ativan", "xanax",
    "klonopin", "valium", "restoril", "ambien", "lunesta", "seroquel",
    "zyprexa", "risperdal", "abilify", "geodon", "haldol", "clozaril",
    "latuda", "invega", "lithobid", "depakote", "lamictal", "tegretol",
    "topamax", "keppra", "dilantin", "neurontin", "lyrica",
    "coumadin", "eliquis", "xarelto", "pradaxa", "plavix", "brilinta",
    "lipitor", "crestor", "zocor", "lopressor", "toprol", "coreg",
    "lasix", "aldactone", "norvasc", "zestril", "cozaar", "diovan",
    "glucophage", "januvia", "jardiance", "farxiga", "ozempic",
    "lantus", "humalog", "synthroid", "prednisone", "medrol",
    "augmentin", "zithromax", "levaquin", "cipro", "flagyl", "bactrim",
    "rocephin", "zosyn", "vancomycin", "zyvox", "diflucan", "valtrex",
    "tamiflu", "paxlovid", "protonix", "prilosec", "zofran", "reglan",
    "narcan", "naloxone", "naltrexone", "suboxone", "methadone",
    "antabuse", "campral", "chantix", "romazicon", "flumazenil",
    "dantrium", "dantrolene", "parlodel", "bromocriptine", "cogentin",
    "argatroban", "bivalirudin", "fondaparinux", "lovenox", "heparin",
}

# Tokens that only ever occur INSIDE a clinical phrase: connectives and
# the Latin/French tails of fixed terms ("torsades de pointes",
# "polycythemia vera", "carcinoma in situ", "post partum").
_CONNECTIVES = frozenset({"de", "of", "en", "la", "le", "von", "van", "and", "the",
                          "in", "et", "pointes", "vera", "situ", "cordis",
                          "partum", "mortem",
                          # Latin anatomical tails: "gluteus maximus",
                          # "pectoralis major", "fascia lata".
                          "maximus", "medius", "minimus", "longus", "brevis",
                          "magnus", "profundus", "superficialis", "communis",
                          "propria", "lata", "externus", "internus",
                          "anterior", "posterior", "lateralis", "medialis"})

# Generic-drug morphology: these stems are strong signals a token is a
# medication, not a person. Catches thousands of generics without a
# giant hardcoded list (e.g. -olol, -pril, -statin, -azepam).
DRUG_SUFFIXES: tuple[str, ...] = (
    "olol", "pril", "sartan", "statin", "azepam", "azolam", "barbital",
    "tidine", "prazole", "dipine", "afil", "cycline", "cillin", "mycin",
    "micin", "oxacin", "floxacin", "cephin", "ceftin", "sulfa", "azole",
    "avir", "ivir", "ovir", "uvir", "evir", "navir", "mab", "nib", "tinib", "ciclib", "parib",
    "setron", "triptan", "caine", "curium", "onium", "phylline", "terol",
    "tropium", "asone", "solone", "onide", "orphan", "codone", "morphone",
    "fentanil", "ketamine", "zosin", "terone", "gliptin", "glitazone",
    "gliflozin", "tide", "ximab", "zumab", "umab", "prost", "dronate",
    "oxifene", "tamide", "rubicin", "platin", "taxel", "citabine",
)

# ---------------------------------------------------------------------
# Clinical measurements and structured data that must survive intact.
# If "lithium 900 mg" or "K+ 6.2" is masked, a reader of the redacted
# note loses the dose or the value. These are NOT PHI under HIPAA and
# must never be redacted.
# ---------------------------------------------------------------------
CLINICAL_UNITS: tuple[str, ...] = (
    "mg", "mcg", "g", "kg", "ml", "l", "dl", "meq", "mmol", "mol",
    "mosm", "iu", "units", "mmhg", "cmh2o", "bpm", "mg/dl", "meq/l",
    "mmol/l", "ng/ml", "pg/ml", "mcg/dl", "g/dl", "u/l", "iu/l",
    "mm/hr", "fl", "pg", "%", "mg/kg", "mcg/kg/min", "ml/kg",
)

# Common clinical abbreviations that look like initials/IDs to NER.
CLINICAL_ABBREVIATIONS: set[str] = {
    "bp", "hr", "rr", "spo2", "sao2", "fio2", "temp", "afeb", "wnl",
    "cbc", "bmp", "cmp", "lft", "abg", "vbg", "ua", "ekg", "ecg",
    "cxr", "ct", "mri", "mra", "cta", "us", "echo", "tte", "tee",
    "bun", "cr", "gfr", "egfr", "hgb", "hct", "wbc", "plt", "mcv",
    "rdw", "mchc", "inr", "ptt", "aptt", "ldh", "crp", "esr", "bnp",
    "tsh", "ft4", "t3", "hba1c", "ldl", "hdl", "tg", "ast", "alt",
    "alp", "ggt", "tbili", "dbili", "psa", "cea", "afp", "ca125",
    "copd", "chf", "hfref", "hfpef", "cad", "mi", "stemi", "nstemi",
    "pe", "dvt", "cva", "tia", "sah", "sdh", "edh", "ich", "icp",
    "aki", "ckd", "esrd", "rta", "atn", "gn", "nsaid", "ppi", "ssri",
    "snri", "maoi", "tca", "dmard", "tnf", "acei", "arb", "ccb",
    "dka", "hhs", "sbp", "hit", "aiha", "itp", "ttp", "dic", "hus",
    "gbs", "ms", "als", "pmr", "gca", "ra", "sle", "aps", "oa",
    "uti", "cap", "hap", "vap", "sirs", "ards", "osa", "pft", "lp",
    "csf", "ivf", "ns", "lr", "po", "iv", "im", "sq", "prn", "bid",
    "tid", "qid", "qhs", "qd", "npo", "hx", "pmh", "fhx", "shx", "ros",
    "dx", "ddx", "tx", "rx", "sx", "hpi", "pe", "a&o", "nad", "rrr",
    "ctab", "sob", "doe", "pnd", "loc", "ams", "n/v", "abd",
}

# Chart shorthand for time around an operation and for services.
# "Postop Dx:" must not lose "Postop" as a name. The mined vocabulary
# (lexicon_data_v2.py) lists "dispo" and "neuro" among the ambiguous
# eponyms; they are nobody's name in any list we hold, so they are restored below
# (after the ambiguous set is subtracted) — the name guard still applies.
CHART_SHORTHAND = frozenset({
    "postop", "preop", "intraop", "periop", "post-op", "pre-op", "intra-op",
    "peri-op", "postoperatively", "preoperatively", "intraoperatively",
    "dispo", "recs", "neuro", "psych", "ortho", "cardio", "pulm", "gyn",
    "peds", "heme", "onc", "nsgy", "neurosurg", "gensurg", "pacu", "sicu",
    "micu", "nicu", "picu", "cvicu", "tdap", "hemovac", "nonfocal",
})
CLINICAL_ABBREVIATIONS |= CHART_SHORTHAND

# Common chart abbreviations of THREE or more letters the ontologies do not
# tag (found through the agate). Two-letter forms are
# kept out of detection on purpose — they would shield initials — and live
# in the gate's own list (inkguard/agate.py). A form that is also a
# name ("ED" / "Ed") keeps its clinical meaning in capitals only, through
# the acronym-collision rule below.
CHART_ABBREVIATIONS = frozenset("""
    kub ivp nkda nka ebl eomi perrl perrla wnl arom prom dtr dtrs orif cta ctab rrr nad bmp cmp
    cbc ucx cxr abg vbg lft lfts tpn ppn picc ngt ogt ttp bsa bmi adl adls iadl aaox3 aox3 aao
    gcs nihss cvp icp svr pvr pacu sicu micu nicu picu ccu cvicu dnr dni polst molst hcp poa
    ama lwbs brbpr nsvd svd ltcs vbac ega lmp edc edd fht fhr nst bpp afi srom pprom ruq luq
    rlq llq nsr jvd uop geta hld lbp eua ehl avf ntg avh biba bhb hcm rcri lof sve abvd avd
    pcn gtt exac rhabdo incr yest appy chole lap hpi ros pmh psh adhd ptsd gerd copd chf cad
    htn hld dm2 ckd esrd aki uti cap hcap vte dvt ped peds ivdu etoh hep afib aflutter svt
    vtach vfib pea rosc cpr acls bls tele sbp dbp spo2 fio2 peep bipap cpap hfnc nrb ett
    lma rsi cabg pci stemi nstemi tavr tee tte echo ekg eeg emg ncs mri mra cta ctpa vq
    ercp egd eus mrcp tips lvp paracentesis thora ed er icu
    wbat nwb pwb ttwb rle lle rue lue piv pivs txa rvr atfl tka tha tsa ppx hctz
    adm wcc pcp neg pos signout sbo vus rrso cbit cvat ava attg proc imms
""".split())
CLINICAL_ABBREVIATIONS |= CHART_ABBREVIATIONS

_ALL_CLINICAL_TERMS = (
    EPONYMS | CLINICAL_SCALES | DRUG_NAMES | CLINICAL_ABBREVIATIONS
)

# ---------------------------------------------------------------------
# Non-eponymous clinical terms that NER still misreads. Latin-derived
# and single-capitalized clinical nouns are the usual offenders:
# "Tamponade", "Pulsus paradoxus", "Torsades" all get tagged PERSON
# when they appear capitalized at the start of a clause.
# ---------------------------------------------------------------------
CLINICAL_CONDITIONS: set[str] = {
    # Signs / exam findings
    "pulsus", "paradoxus", "parvus", "tardus", "alternans", "bigeminus",
    "tamponade", "bruit", "thrill", "rales", "rhonchi", "crackles",
    "wheeze", "stridor", "egophony", "fremitus", "dullness", "ascites",
    "clubbing", "cyanosis", "pallor", "jaundice", "icterus", "petechiae",
    "purpura", "ecchymosis", "edema", "anasarca", "orthopnea",
    "hepatomegaly", "splenomegaly", "lymphadenopathy", "nystagmus",
    "ptosis", "miosis", "mydriasis", "anisocoria", "papilledema",
    "asterixis", "clonus", "fasciculations", "rigidity", "bradykinesia",
    "dysarthria", "dysphagia", "aphasia", "ataxia", "apraxia", "agnosia",
    "hemiparesis", "hemiplegia", "paraplegia", "quadriplegia",
    "hyperreflexia", "hyporeflexia", "areflexia", "allodynia",
    # Cardiac / pulm
    "angina", "infarction", "ischemia", "arrhythmia", "fibrillation",
    "flutter", "tachycardia", "bradycardia", "asystole", "torsades",
    "cardiomyopathy", "pericarditis", "myocarditis", "endocarditis",
    "stenosis", "regurgitation", "prolapse", "aneurysm", "dissection",
    "embolism", "thrombosis", "atelectasis", "consolidation",
    "pneumothorax", "hemothorax", "effusion", "empyema", "bronchiectasis",
    "emphysema", "fibrosis", "sarcoidosis", "hypoxemia", "hypercapnia",
    # Renal / metabolic / endocrine
    "azotemia", "uremia", "proteinuria", "hematuria", "oliguria",
    "anuria", "polyuria", "nocturia", "glomerulonephritis", "nephritis",
    "nephropathy", "nephrotic", "nephritic", "acidosis", "alkalosis",
    "ketoacidosis", "hyperkalemia", "hypokalemia", "hyponatremia",
    "hypernatremia", "hypercalcemia", "hypocalcemia", "hypoglycemia",
    "hyperglycemia", "hypothyroidism", "hyperthyroidism", "thyrotoxicosis",
    "myxedema", "acromegaly", "gigantism", "pheochromocytoma",
    "hyperaldosteronism", "hypopituitarism", "insipidus", "mellitus",
    # Heme / onc / ID
    "anemia", "thalassemia", "sideroblastic", "hemolysis", "hemolytic",
    "thrombocytopenia", "thrombocytosis", "neutropenia", "leukocytosis",
    "leukopenia", "pancytopenia", "coagulopathy", "leukemia", "lymphoma",
    "myeloma", "myelodysplastic", "carcinoma", "sarcoma", "adenoma",
    "metastasis", "metastatic", "neoplasm", "sepsis", "septicemia",
    "bacteremia", "meningitis", "encephalitis", "osteomyelitis",
    "cellulitis", "abscess", "pyelonephritis", "cholangitis",
    "cholecystitis", "pancreatitis", "appendicitis", "diverticulitis",
    "peritonitis", "colitis", "gastritis", "esophagitis", "hepatitis",
    "cirrhosis", "steatosis", "varices", "encephalopathy",
    # Rheum / neuro / psych
    "arthritis", "arthralgia", "myalgia", "myositis", "vasculitis",
    "polymyalgia", "fibromyalgia", "spondylitis", "ankylosing",
    "osteoporosis", "osteopenia", "osteoarthritis", "bursitis",
    "tendinitis", "enthesitis", "neuropathy", "radiculopathy",
    "myelopathy", "myopathy", "seizure", "epilepsy", "syncope",
    "vertigo", "migraine", "dementia", "delirium", "psychosis",
    "catatonia", "akathisia", "dystonia", "dyskinesia", "parkinsonism",
    "mania", "hypomania", "dysthymia", "anhedonia", "avolidia",
    "avolition", "alogia", "abulia", "anxiolysis", "withdrawal",
    "intoxication", "dependence", "tolerance",
    # Obstetric / misc
    "preeclampsia", "eclampsia", "abruption", "previa", "gestational",
    "parity", "gravida", "para", "trimester", "puerperium",
    "prophylaxis", "empiric", "idiopathic", "iatrogenic", "nosocomial",
    "refractory", "fulminant", "indolent", "occult", "prodrome",
    "sequelae", "morbidity", "mortality", "differential", "workup",
}

_ALL_CLINICAL_TERMS |= CLINICAL_CONDITIONS

# Dosing frequency and administration timing. These read as dates to
# NER ("daily", "nightly", "q6h") but they are dosing instructions —
# masking them turns "prednisone 60 mg daily" into an unspecified
# regimen a reader of the redacted note cannot safely interpret.
DOSING_FREQUENCY: set[str] = {
    "daily", "nightly", "weekly", "monthly", "hourly", "once", "twice",
    "thrice", "bid", "tid", "qid", "qhs", "qam", "qpm", "qd", "qod",
    "prn", "stat", "ac", "pc", "q4h", "q6h", "q8h", "q12h", "q24h",
    "continuous", "infusion", "bolus", "taper", "morning", "evening",
    "bedtime", "mealtime", "divided", "doses", "dose", "dosing",
}

_ALL_CLINICAL_TERMS |= DOSING_FREQUENCY

# ---------------------------------------------------------------------
# OPEN VOCABULARY (see lexicon_open.py, THIRD_PARTY_NOTICES.md).
#
# Single words from the names, synonyms and definitions of openly
# licensed or public-domain sources: Uberon, Disease Ontology, MONDO,
# MAxO, the NCI Thesaurus, ICD-10-CM and WordNet 3.0 (general-English
# noun files). Each entry in lexicon_open.py names the source(s) that
# attest it.
#
# Single ontology words enter the WEAK vocabulary tier (is_vocabulary_word:
# a title or relation word before them always wins). Ontology eponyms
# that are proper nouns, or anybody's name, join the ambiguous set:
# clinical only with a qualifier.
# ---------------------------------------------------------------------
from inkguard.lexicon_open import OPEN_VOCAB, OPEN_EPONYMS_AMBIGUOUS  # noqa: E402

# Second mining pass: device, drug and diagnosis vocabulary from FDA
# GUDID, FDA drug listings and ICD-10-CM (see lexicon_data_v2.py; the
# per-term sources are in tools/lexmine/lexicon_data_v2_sources.csv.gz).
# Added to close the gap measured on the 200-document
# benchmark, where anatomical and procedural terms were being redacted
# as people and places.
#
# Every eponym from that pass is AMBIGUOUS by construction and is
# folded in below with the other ambiguous terms, never here.
from inkguard.lexicon_data_v2 import (  # noqa: E402
    MINED_V2_UNAMBIGUOUS,
    MINED_V2_AMBIGUOUS,
)

CLINICAL_CONDITIONS |= MINED_V2_UNAMBIGUOUS
_ALL_CLINICAL_TERMS |= MINED_V2_UNAMBIGUOUS

# ---------------------------------------------------------------------
# AMBIGUOUS EPONYMS — eponyms that are ALSO common personal names.
# These are REMOVED from the protected set and only treated as clinical
# when accompanied by a disease qualifier. This prevents the tool
# from "protecting" (i.e. leaking) a patient actually named Wilson,
# Jones, Bell, Graves or Smith.
# ---------------------------------------------------------------------
AMBIGUOUS_EPONYMS: set[str] = {
    "wilson", "jones", "smith", "bell", "bells", "graves", "weber",
    "duke", "marcus", "gunn", "still", "stills", "hunt", "rich", "todd",
    "beck", "light", "lights", "down", "downs", "turner", "gilbert",
    "christmas", "evans", "gram", "wright", "hodgkin", "hodgkins",
    "paget", "pagets", "pott", "potts", "colles", "barlow", "adie",
    "morton", "baker", "carney", "conn", "conns", "nelson", "rotor",
    "johnson", "sweet", "williams", "becker", "edwards", "brown",
    "cushing", "cushings", "addison", "addisons", "parkinson",
    "parkinsons", "huntington", "alzheimer", "alzheimers",
} | frozenset(OPEN_EPONYMS_AMBIGUOUS) | MINED_V2_AMBIGUOUS

# Remove ambiguous names from the auto-protect set. They now require
# a qualifier to count as clinical.
_ALL_CLINICAL_TERMS -= AMBIGUOUS_EPONYMS
from inkguard.name_registry import NAMES as _REGISTRY  # noqa: E402
AMBIGUOUS_EPONYMS -= {w for w in CHART_SHORTHAND if w not in _REGISTRY}
_ALL_CLINICAL_TERMS |= {w for w in CHART_SHORTHAND if w not in _REGISTRY}

# OPERATING-ROOM CONSUMABLES. Sutures, dressings, hemostatics and
# adhesives named in nearly every operative note, several of which the
# mined sources missed ("Monocryl" was absent from all of them). Curated
# by hand; every entry is still passed through the name guard below.
OR_CONSUMABLES = frozenset({
    "monocryl", "prolene", "vicryl", "ethilon", "nurolon", "ethibond",
    "maxon", "biosyn", "caprosyn", "polysorb", "surgilon", "pds", "chromic",
    "dermabond", "mastisol", "benzoin", "steri-strips", "steristrips",
    "xeroform", "kerlix", "coban", "tegaderm", "telfa", "adaptic", "opsite",
    "surgicel", "gelfoam", "floseal", "thrombin", "avitene", "tisseel",
    "bovie", "ligaclips", "ligaclip", "hemoclips", "endoloop", "endogia",
    "merocel", "provisc", "viscoat", "healon", "duovisc", "miochol",
    "wydase", "hylenex", "betadine", "chloraprep", "duraprep", "hibiclens",
    "marcaine", "sensorcaine", "xylocaine", "exparel", "toradol",
})
_ALL_CLINICAL_TERMS |= OR_CONSUMABLES

# NEUROSURGICAL CARE (lexicon_neuro.py): the words of neurosurgery and the
# specialties around its patients. Ordinary English words of the phrases
# ("and", "with", "test") are left out: only the clinical words are added,
# and the name guard below demotes any that is also someone's name.
from inkguard.lexicon_neuro import (  # noqa: E402
    words as _neuro_words, NEURO_QUALIFIERS, all_products as _all_products)
NEURO_PRODUCTS = _all_products()
_BEFORE_NEURO = frozenset(_ALL_CLINICAL_TERMS)
_NEURO_STOP = frozenset("""
    a an and the of with at to in on for or by due from into after before next week open
    stored type grade class stage scale score test sign line point time level
""".split())
def _everyday(w: str) -> bool:
    """A word of ordinary English ("family", "home", "visit"). Making it a
    clinical term would make the words around a name look clinical, and
    "Kerrison family at bedside" was left unredacted. Only the specialist words
    of a phrase are added."""
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency(w, "en") >= 3.5
    except Exception:      # without the frequency list, add nothing doubtful
        return True


NEURO_TERMS = frozenset(w for w in _neuro_words(proper=False)
                        if len(w) >= 3 and w not in _NEURO_STOP and not w[0].isdigit()
                        and (w in _BEFORE_NEURO or not _everyday(w)))
# Words written with a capital (Kerrison, Mayfield, Penfield, Leksell) are
# eponyms and companies: anyone may carry the name, so they are clinical
# only beside a clinical word, whatever the name lists say.
# Words the vocabulary already held unambiguously (Chiari, Babinski) keep
# their standing; product names (NEURO_PRODUCTS) are clinical on their
# own, unless the name guard below finds them in the name registry.
NEURO_PROPER = (frozenset(w for w in _neuro_words(proper=True)
                          if len(w) >= 3 and w not in _NEURO_STOP)
                - NEURO_TERMS - _BEFORE_NEURO - NEURO_PRODUCTS)
_ALL_CLINICAL_TERMS |= NEURO_TERMS | NEURO_PRODUCTS

# NAME GUARD. Clinical words that are also first or last names in the
# name registry (Hicks, Norton, Robertson, Stokes, Doe; 110 words at
# present) would otherwise let a patient with that surname be subtracted
# as "medical vocabulary" and left unredacted.
# They are demoted to ambiguous: clinical only with a qualifier. Upper-
# case short forms keep their acronym meaning (TIA, AKI, HUS, US) through
# _ACRONYM_COLLISIONS; "Tia" the person is still a name.
from inkguard.name_registry import NAMES as _NAMES  # noqa: E402

_NAME_COLLISIONS = frozenset(t for t in _ALL_CLINICAL_TERMS if t in _NAMES)
_ACRONYM_COLLISIONS = frozenset(t for t in _NAME_COLLISIONS if len(t) <= 4)
_ALL_CLINICAL_TERMS -= _NAME_COLLISIONS
AMBIGUOUS_EPONYMS |= _NAME_COLLISIONS
# NEURO_PROPER words are NOT added to the ambiguous eponyms: that set also
# lowers how name-like a word scores, and "Kerrison family at bedside" was
# left unredacted. They only open a clinical phrase that reaches a clinical word
# ("Kerrison rongeur"), through the chain checks below.
_ALL_CLINICAL_TERMS -= NEURO_PROPER

# Structural medical words that qualify an ambiguous eponym into a
# clinical concept.
DISEASE_QUALIFIERS: set[str] = {
    # DEVICE QUALIFIERS. Device eponyms behave exactly like disease
    # eponyms: "Foley" is a catheter and a common surname,
    # "Bovie" is an electrosurgical unit and a surname, "Kegel" is an
    # exercise and a surname. The registry pass files all of them as
    # ambiguous, which is correct -- but without a device word here
    # nothing can ever resolve them, so "Foley catheter" stayed
    # over-redacted on the benchmark even after the term was
    # classified properly. The qualifier list is what makes ambiguity
    # survivable; it has to cover the operating room, not just the ward.
    "catheter", "needle", "suture", "clamp", "retractor", "forceps",
    "scope", "drain", "stent", "graft", "prosthesis", "trocar",
    "cannula", "speculum", "dilator", "probe", "tube", "sponge",
    "solution", "scrub", "dressing", "splint", "mesh", "balloon",
    "sheath", "blade", "elevator", "rongeur", "curette", "bougie",
    "shunt", "valve", "filter", "clip", "stapler", "electrode",
    "prep", "gauze", "tourniquet", "osteotome", "exercises", "unit",
    "procedure", "technique", "approach", "incision", "flap", "repair",

    "syndrome", "disease", "disorder", "sign", "score", "scale",
    "criteria", "test", "triad", "reflex", "phenomenon", "maneuver",
    "encephalopathy", "palsy", "lymphoma", "nodes", "lesions", "bodies",
    "cells", "deformity", "fracture", "murmur", "stain", "factor",
    "tumor", "carcinoma", "anemia", "ataxia", "dystrophy", "chorea",
    "fistula", "ulcer", "pulse", "index", "classification", "staging",
    # Added from corpus review: these follow eponyms often enough that
    # omitting them causes real over-redaction ("Roth spots" → <PERSON>
    # spots). Extend this list whenever a demoted eponym turns up
    # over-redacted in the field — that is cheaper and safer than
    # promoting the eponym back to unambiguous.
    "spots", "granulomatosis", "sarcoma", "cyst", "diverticulum",
    "canal", "duct", "space", "ligament", "gland", "plexus", "artery",
    "vein", "nerve", "fossa", "angle", "line", "point", "ring", "law",
    "rule", "method", "procedure", "operation", "shunt", "grade",
    "stage", "phase", "response", "crisis", "attack", "type", "variant",
    "class", "stain", "level", "ratio", "count", "curve", "gradient",
    "tract", "fiber", "fibers", "cycle", "pathway", "receptor",
} | set(NEURO_QUALIFIERS)


def eponym_phrase_continues(first: str, following: list[str]) -> bool:
    """Does the eponym `first` OPEN a clinical phrase that runs, without
    a break, into a disease qualifier?

    Anchored at `first` on purpose. A chain that may start anywhere lets
    "Smith has Wilson disease" mark SMITH as clinical, because a
    different eponym later in the clause reaches a qualifier. The
    question is always about the hit's own token.
    """
    if first not in AMBIGUOUS_EPONYMS and first not in NEURO_PROPER:
        return False
    for i, nxt in enumerate(following):
        if nxt in DISEASE_QUALIFIERS:
            return True
        if nxt in AMBIGUOUS_EPONYMS or nxt in _ALL_CLINICAL_TERMS or nxt in NEURO_PROPER:
            continue
        # Paired eponyms: "Hunt and Hess grade", "Hoehn and Yahr stage".
        # "and" carries the chain only into another eponym.
        if nxt == "and" and i + 1 < len(following) and (
                following[i + 1] in AMBIGUOUS_EPONYMS or following[i + 1] in _ALL_CLINICAL_TERMS):
            continue
        return False
    return False


def _eponym_chain_reaches_qualifier(tokens: list[str]) -> bool:
    """True if some ambiguous eponym is followed, in order and without a
    break, by a disease qualifier. Intermediate tokens must themselves be
    eponyms or clinical terms — the phrase must stay clinical all the way
    to the qualifier."""
    for i, tok in enumerate(tokens):
        if tok not in AMBIGUOUS_EPONYMS and tok not in NEURO_PROPER:
            continue
        rest = tokens[i + 1:]
        for j, nxt in enumerate(rest):
            if nxt in DISEASE_QUALIFIERS:
                return True
            if nxt in AMBIGUOUS_EPONYMS or nxt in _ALL_CLINICAL_TERMS or nxt in NEURO_PROPER:
                continue
            if nxt == "and" and j + 1 < len(rest) and (
                    rest[j + 1] in AMBIGUOUS_EPONYMS or rest[j + 1] in _ALL_CLINICAL_TERMS):
                continue
            break
    return False


import re  # noqa: E402

from inkguard.lexicon_icd_vocab import ICD_VOCAB as _ICD_ONLY  # noqa: E402

# The weak vocabulary tier: ICD-10-CM description words plus the open
# ontology vocabulary. Name-guarded at build time; title-vetoable at run
# time. Kept under the name ICD_VOCAB for the modules that import it.
# (A further weak vocabulary from MeSH, HL7, OMRSE, CVX, UO, SYMP and MF was
# built and measured — tools/build_care_vocab.py — and NOT
# adopted: it lowered identifier recall on the main benchmark's tuning half
# (92.9% -> 92.5%, five more notes blocked) for a 0.1-point gain in clinical
# text kept. Plant and place words that are also first names slipped past
# the name lists.)
ICD_VOCAB = frozenset(_ICD_ONLY) | (frozenset(OPEN_VOCAB) - AMBIGUOUS_EPONYMS)


def is_vocabulary_word(text: str) -> bool:
    """Is every token of this span open clinical vocabulary?

    The WEAK tier: ICD-10-CM description words plus the ontology and
    WordNet vocabulary (lexicon_open.py), all name-guarded at build time
    against the name registry and the US Census name files. No name list
    is complete, so callers must let a name title or relation word
    before the span override this — "urethra" is anatomy, "Dr. Fluke" is not.

    A token not in the vocabulary is also accepted when it DECOMPOSES
    into medical parts (see _decomposes): "transpedicular" = trans +
    pedicular, "laminotomies" = laminotomy + plural, "subacromially" =
    sub + acromial + ly.
    """
    t = text.strip().lower().strip(".,;:()[]'\"")
    t = t.replace("'s", "").replace("’s", "")
    if t in ICD_VOCAB:                 # whole compounds: "beckwith-wiedemann"
        return True
    toks = [x for x in re.split(r"[\s\-]+", t) if x]
    known = lambda x: x in ICD_VOCAB or _decomposes(x)
    return bool(toks) and all(known(x) or x in _ALL_CLINICAL_TERMS for x in toks) \
        and any(known(x) for x in toks)


# MORPHOLOGY. Medical words are built from parts; a word we have never
# listed is still recognisable when its parts are (prefix, root, suffix).
_INFLECTIONS = (("ies", "y"), ("ies", "ie"), ("es", ""), ("s", ""), ("ally", "al"),
                ("ly", ""), ("ically", "ic"), ("ae", "a"), ("i", "us"), ("ata", "a"))
_PREFIXES = tuple(sorted((
    "trans", "sub", "supra", "infra", "intra", "inter", "peri", "para", "retro",
    "post", "pre", "epi", "hypo", "hyper", "endo", "ecto", "extra", "micro",
    "macro", "muco", "myo", "neuro", "osteo", "cardio", "gastro", "hepato",
    "nephro", "uro", "cysto", "lapar", "laparo", "arthro", "angio", "broncho",
    "thoraco", "cranio", "spondylo", "costo", "chondro", "vaso", "hemi", "semi",
    "anti", "re", "non", "poly", "pan", "de", "dys", "circum", "contra",
    "ante", "medio", "latero", "dorso", "ventro", "antero", "postero",
    "cervico", "lumbo", "sacro", "ilio", "ischio", "pubo", "femoro", "tibio",
    "fibulo", "radio", "ulno", "humero", "scapulo", "acromio", "coraco",
    "sterno", "cleido", "naso", "oro", "pharyngo", "laryngo", "tracheo",
    "esophago", "entero", "colo", "procto", "recto", "vesico", "urethro",
    "utero", "salpingo", "oophoro", "hystero", "mammo", "phaco", "kerato",
    "retino", "blepharo", "oto", "rhino", "sino", "dento", "gingivo",
), key=len, reverse=True))


def _decomposes(word: str, depth: int = 0) -> bool:
    w = word.lower()
    if depth > 3 or len(w) < 6 or not w.isalpha():
        return False
    if depth and (w in ICD_VOCAB or w in _ALL_CLINICAL_TERMS):
        return True
    # Surnames never decompose: "Transue", "Subramanian", "Parrish" are
    # guarded by the same registry that guards the vocabulary.
    if depth == 0 and w in _NAMES:
        return False
    for suf, rep in _INFLECTIONS:
        if w.endswith(suf) and len(w) - len(suf) >= 5:
            if _decomposes(w[: len(w) - len(suf)] + rep, depth + 1):
                return True
    for pre in _PREFIXES:
        if w.startswith(pre) and len(w) - len(pre) >= 5:
            rest = w[len(pre):]
            if _decomposes(rest, depth + 1) or (rest in ICD_VOCAB):
                return True
    return False


def is_clinical_term(text: str) -> bool:
    """True if this span is a known medical concept and therefore
    NOT PHI, regardless of what the NER model thinks.

    AMBIGUITY HANDLING — read this before editing the lexicon.
    Many eponyms are also common surnames: Wilson, Jones, Smith, Bell,
    Graves, Weber, Duke, Marcus, Still, Hunt. A patient named Wilson is
    PHI; "Wilson disease" is not. Treating the bare token as clinical
    would leak real patient names — so ambiguous eponyms only count as
    clinical when paired with a disease qualifier ("Wilson disease",
    "Bell palsy"). Unambiguous eponyms (Wernicke, Korsakoff, Crohn,
    Guillain-Barre) pass on their own because no realistic patient-name
    collision exists.

    When in doubt, put a term in AMBIGUOUS_EPONYMS. The cost of a
    missed clinical-term protection is one over-redaction, which a
    reader can see in the redacted copy; the cost of a wrong protection is a
    leaked patient name.
    """
    t = text.strip().lower().strip(".,;:()[]'\"")
    if not t:
        return False

    base = t.replace("'s", "").replace("’s", "")

    # Unambiguous single-token clinical concept
    if base in _ALL_CLINICAL_TERMS:
        return True
    # Acronym written as an acronym: "TIA", "AKI" — not "Tia".
    raw = text.strip().strip(".,;:()[]'\"")
    if raw.isupper() and base in _ACRONYM_COLLISIONS:
        return True

    tokens = [tok for tok in base.replace("-", " ").split() if tok]
    if not tokens:
        return False

    # Ambiguous eponym + disease qualifier ("Wilson disease").
    #
    # ORDER AND CONTINUITY MATTER. The earlier rule was "any eponym token
    # and any qualifier token, anywhere in the span", which made
    # "Mcintyre December Stage" clinical — "mcintyre" is a GUDID cannula
    # brand, "stage" is a qualifier, and the two were a sentence apart.
    # The qualifier must FOLLOW the eponym through an unbroken chain of
    # eponym or clinical tokens: "Lambert Eaton myasthenic syndrome"
    # qualifies; "Mcintyre December Stage" does not, because "december"
    # breaks the phrase.
    if _eponym_chain_reaches_qualifier(tokens):
        return True

    # Multi-word span containing an UNAMBIGUOUS clinical term is
    # clinical as a whole: "Torsades de pointes", "Wernicke
    # encephalopathy", "Stevens-Johnson syndrome".
    # GUARD: only for purely alphabetic spans. Without this, a span
    # like "555-0142 has Wernicke" would qualify and leak a phone
    # number. Digits in a span mean it is a structured identifier,
    # and no neighbouring medical word can make it non-PHI.
    if not any(ch.isdigit() for ch in base):
        # A rule of "ANY token is clinical => the span is clinical" would
        # leak names: NER returns "Patrick Doe" as one span, "doe"
        # (dyspnea on exertion) is in the lexicon, so the whole name would
        # be subtracted as medical vocabulary. A span is a clinical PHRASE
        # only when EVERY token belongs to one: a clinical term, a
        # qualifier, an eponym, or a connective ("Torsades de pointes").
        # Multi-token only: a single ICD-vocabulary word is the weaker
        # tier (is_vocabulary_word), which a title must be able to veto.
        if (len(tokens) >= 2
                and all(tok in _ALL_CLINICAL_TERMS or tok in DISEASE_QUALIFIERS
                or tok in AMBIGUOUS_EPONYMS or tok in _CONNECTIVES
                or tok in ICD_VOCAB for tok in tokens)
                and any(tok in _ALL_CLINICAL_TERMS or tok in DISEASE_QUALIFIERS
                        or tok in ICD_VOCAB for tok in tokens)
                and not all(tok in AMBIGUOUS_EPONYMS or tok in _CONNECTIVES
                            for tok in tokens)):
            return True

    # Generic drug morphology — SINGLE tokens only, never a known name.
    # On whole spans, "Ahmed Tanvir" ended in "vir" and read as an
    # antiviral.
    if (len(tokens) == 1 and len(base) > 5 and base not in _NAMES
            and any(base.endswith(suf) for suf in DRUG_SUFFIXES)):
        return True

    return False
