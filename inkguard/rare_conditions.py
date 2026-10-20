"""
Prevalence tiers for conditions whose rarity makes a redacted
record findable, and categories whose exposure causes harm.

SEPARATED FROM THE LOGIC ON PURPOSE. This file is data a clinician or
an epidemiologist can review and correct without reading any code, and
it is the file a reviewer will want to check. Keeping the
scoring logic out of it means a prevalence correction is a one-line
edit, not a code change.

PREVALENCE FIGURES ARE APPROXIMATE AND US-SCOPED. They are used only to
assign a TIER, never reported as a count, because the tool must not
imply a precision it does not have. Where sources disagree the more
conservative (rarer) tier is chosen — over-flagging costs a human
review, under-flagging costs a patient.

THE TIERS ARE APPROXIMATE. The case counts in the tier headers below are
nominal. Several entries sit in a rarer tier than their US count alone
would put them (for example Klinefelter, Marfan and Turner syndromes,
and myasthenia gravis, in VERY_RARE; cerebral palsy in RARE), and a few
may sit in a less rare tier than their count suggests (for example
diabetes insipidus in RARE). The placements have not yet been checked
entry by entry against Orphanet or GARD figures.

EXPANSION PATH: Orphanet publishes prevalence per ORPHAcode under a
free licence, and the NIH GARD list covers ~6,500 more conditions than
the seed below. This module is structured so that data drops in as
additional dict entries with no logic change.
"""

from __future__ import annotations

# =====================================================================
# FINDABILITY — prevalence tiers
#
# Thresholds are nominal US case counts (see THE TIERS ARE APPROXIMATE
# above). The Orphan Drug Act defines "rare" as
# fewer than 200,000 Americans, which is far too broad to be useful as
# a re-identification signal — sickle cell disease is rare by that
# definition and there are ~100,000 US patients. So RARE is subdivided.
# =====================================================================

# Nominally fewer than ~1,000 US cases. At this size a single additional
# attribute — age band, sex, region — very often isolates one person.
ULTRA_RARE: frozenset[str] = frozenset({
    "erdheim-chester disease", "erdheim chester",
    "fibrodysplasia ossificans progressiva",
    "hutchinson-gilford progeria", "progeria",
    "fields disease",
    "ribose-5-phosphate isomerase deficiency",
    "alkaptonuria",
    "lethal congenital contracture syndrome",
    "kuru",
    "fatal familial insomnia",
    "hypophosphatasia perinatal",
    "leukocyte adhesion deficiency",
    "hermansky-pudlak syndrome",
    "carpenter syndrome",
    "cockayne syndrome",
    "aicardi syndrome",
    "walker-warburg syndrome",
    "microcephalic osteodysplastic primordial dwarfism",
    "cantu syndrome",
    "proteus syndrome",
    "stoneman syndrome",
    "xeroderma pigmentosum",
    "harlequin ichthyosis",
    "moebius syndrome",
    "pentalogy of cantrell",
    "chronic infantile neurologic cutaneous articular syndrome",
})

# Nominally fewer than ~20,000 US cases (approximate; some entries are
# more common and are placed here conservatively). Rare enough that a rare condition plus
# two retained demographics is usually a very small group.
VERY_RARE: frozenset[str] = frozenset({
    "wilson disease",
    "gaucher disease",
    "pompe disease",
    "fabry disease",
    "niemann-pick disease",
    "tay-sachs disease",
    "maple syrup urine disease",
    "phenylketonuria",
    "adrenoleukodystrophy",
    "metachromatic leukodystrophy",
    "krabbe disease",
    "canavan disease",
    "prader-willi syndrome",
    "angelman syndrome",
    "rett syndrome",
    "williams syndrome",
    "dravet syndrome",
    "lennox-gastaut syndrome",
    "creutzfeldt-jakob disease",
    "progressive supranuclear palsy",
    "corticobasal degeneration",
    "multiple system atrophy",
    "spinal muscular atrophy",
    "friedreich ataxia",
    "charcot-marie-tooth disease",
    "lambert-eaton myasthenic syndrome",
    "stiff person syndrome",
    "neuromyelitis optica",
    "susac syndrome",
    "behcet disease",
    "takayasu arteritis",
    "granulomatosis with polyangiitis",
    "eosinophilic granulomatosis with polyangiitis",
    "goodpasture syndrome",
    "paroxysmal nocturnal hemoglobinuria",
    "aplastic anemia",
    "castleman disease",
    "waldenstrom macroglobulinemia",
    "hairy cell leukemia",
    "mycosis fungoides",
    "sezary syndrome",
    "pheochromocytoma",
    "carcinoid syndrome",
    "adrenocortical carcinoma",
    "cholangiocarcinoma",
    "mesothelioma",
    "osteosarcoma",
    "ewing sarcoma",
    "rhabdomyosarcoma",
    "retinoblastoma",
    "neuroblastoma",
    "wilms tumor",
    "langerhans cell histiocytosis",
    "hemophagocytic lymphohistiocytosis",
    "amyloidosis",
    "sarcoidosis cardiac",
    "idiopathic pulmonary fibrosis",
    "lymphangioleiomyomatosis",
    "alpha-1 antitrypsin deficiency",
    "primary ciliary dyskinesia",
    "kartagener syndrome",
    "ehlers-danlos vascular",
    "marfan syndrome",
    "osteogenesis imperfecta",
    "achondroplasia",
    "turner syndrome",
    "klinefelter syndrome",
    "fragile x syndrome",
    "di george syndrome",
    "noonan syndrome",
    "tuberous sclerosis",
    "neurofibromatosis type 2",
    "von hippel-lindau disease",
    "li-fraumeni syndrome",
    "peutz-jeghers syndrome",
    "hereditary hemorrhagic telangiectasia",
    "myasthenia gravis",
})

# Nominally fewer than ~200,000 US cases (the statutory definition;
# approximate, and cerebral palsy is more common). On its own
# this is weak evidence; it matters in combination.
RARE: frozenset[str] = frozenset({
    "cystic fibrosis",
    "amyotrophic lateral sclerosis",
    "huntington disease",
    "sickle cell disease",
    "thalassemia",
    "hemophilia",
    "von willebrand disease",
    "scleroderma",
    "systemic sclerosis",
    "dermatomyositis",
    "polymyositis",
    "narcolepsy",
    "myotonic dystrophy",
    "duchenne muscular dystrophy",
    "becker muscular dystrophy",
    "guillain-barre syndrome",
    "chronic inflammatory demyelinating polyneuropathy",
    "transverse myelitis",
    "pulmonary arterial hypertension",
    "primary biliary cholangitis",
    "primary sclerosing cholangitis",
    "autoimmune hepatitis",
    "addison disease",
    "cushing disease",
    "acromegaly",
    "diabetes insipidus",
    "multiple myeloma",
    "myelodysplastic syndrome",
    "myelofibrosis",
    "polycythemia vera",
    "essential thrombocythemia",
    "chronic myeloid leukemia",
    "neurofibromatosis type 1",
    "spina bifida",
    "cerebral palsy",
})

PREVALENCE_TIERS: list[tuple[str, frozenset[str]]] = [
    ("ultra_rare", ULTRA_RARE),
    ("very_rare", VERY_RARE),
    ("rare", RARE),
]


# =====================================================================
# HARM IF EXPOSED — sensitive categories
#
# A SEPARATE AXIS FROM RARITY, and the reason a pure k-anonymity score
# is insufficient. HIV is not rare. Exposure of an HIV status can still
# cost someone employment, insurance, immigration status, custody, or
# physical safety. Population size does not measure that.
#
# These categories are drawn from where documented discrimination and
# concrete legal jeopardy actually occur. Each
# entry is a term likely to survive redaction because it is clinical
# vocabulary, not an identifier.
# =====================================================================
SENSITIVE_CATEGORIES: dict[str, tuple[str, frozenset[str]]] = {
    "hiv_status": (
        "HIV status — employment, insurance, immigration and criminal "
        "exposure in some jurisdictions",
        frozenset({
            "hiv", "hiv-positive", "hiv positive", "hiv/aids",
            "acquired immunodeficiency",
            "antiretroviral", "cd4 count", "viral load",
            "pre-exposure prophylaxis",
            # "aids" is not listed as a bare term: it collides with
            #   "band-aids". "hiv/aids" and "acquired immunodeficiency"
            #   cover the real usage without the collision.
            # "prep" is not listed: PrEP collides with surgical/bowel prep,
            #   which is far more common in operative notes. The
            #   unambiguous full form is kept instead. Case alone cannot
            #   rescue it — notes are inconsistently cased and many are
            #   transcribed in all caps.
        }),
    ),
    "psychiatric": (
        "Psychiatric diagnosis — employment, custody, firearms and "
        "professional-licensing consequences",
        frozenset({
            "schizophrenia", "schizoaffective", "bipolar disorder",
            "psychosis", "psychotic", "borderline personality disorder",
            "antisocial personality disorder", "involuntary commitment",
            "psychiatric hold", "suicidal ideation", "suicide attempt",
            "self-harm", "electroconvulsive therapy",
        }),
    ),
    "substance_use": (
        "Substance use — employment, custody, criminal and "
        "professional-licensing consequences",
        frozenset({
            "opioid use disorder", "alcohol use disorder",
            "substance use disorder", "heroin", "methamphetamine",
            "cocaine dependence", "injection drug use",
            "methadone", "buprenorphine", "suboxone", "naltrexone",
            "overdose", "withdrawal syndrome", "detoxification",
        }),
    ),
    "reproductive": (
        "Reproductive care — criminal exposure in some states, plus "
        "employment and family consequences",
        frozenset({
            "abortion", "termination of pregnancy",
            "elective termination", "induced abortion",
            "dilation and evacuation",
            "mifepristone", "misoprostol",
            # Kept narrow on purpose. Vasectomy, tubal ligation,
            # sterilization and contraception are left out: they are
            # ordinary elective procedures whose disclosure carries
            # little harm, and including them would fire on routine
            # urology and gynecology notes and train a reviewer to
            # ignore the category. This axis measures
            # HARM IF EXPOSED, not topical sensitivity.
        }),
    ),
    "gender_identity": (
        "Gender-affirming care — employment, family, legal and safety "
        "consequences; restricted by law for minors in some states",
        frozenset({
            "gender dysphoria", "gender-affirming",
            "gender affirming hormone therapy",
            "transgender", "transsexual", "cross-sex hormone",
            "puberty blocker", "vaginoplasty", "phalloplasty",
            "orchiectomy gender", "mastectomy gender-affirming",
        }),
    ),
    "sexual_health": (
        "Sexual health — relationship, employment and stigma harm",
        frozenset({
            "syphilis", "gonorrhea", "chlamydia", "herpes simplex 2",
            "genital herpes", "genital warts",
            "sexually transmitted infection", "trichomoniasis",
            # "hpv" is not listed: it appears in routine cervical-cancer
            # screening results constantly, and roughly 80% of adults
            # acquire it. A near-universal finding is not a
            # discriminating signal. "genital warts" is retained because
            # it describes symptomatic disease, not a screening result.
        }),
    ),
    "genetic_familial": (
        "Heritable finding — implicates blood relatives who never "
        "consented to disclosure; insurance consequences",
        frozenset({
            "brca1", "brca2", "lynch syndrome", "huntington disease",
            "familial adenomatous polyposis", "li-fraumeni syndrome",
            "genetic testing", "carrier status", "germline mutation",
            "predictive testing",
        }),
    ),
    "violence_exposure": (
        "Violence or abuse history — physical safety of the patient",
        frozenset({
            "intimate partner violence", "domestic violence",
            "sexual assault", "human trafficking",
            "child abuse", "elder abuse", "restraining order",
        }),
    ),
}
