"""
CARE SETTINGS VOCABULARY.

Where and how a patient is seen changes how a note is written: a hospital
day, a clinic visit, the emergency department, urgent care, a telehealth
call, a controlled-substance check against the state monitoring database,
an infusion centre, dialysis, rehabilitation, a nursing home, a foster
placement, a NICU, a vaccine record, a psychiatric unit, a mobile clinic,
the sideline of a game, a public-health screening, records from another
country, and the notes written when a patient dies.

Each setting has its own words. They are clinical content and are kept.
Each setting also has its own identifiers (a pharmacist's name, a nursing
home's name, a telehealth link). This file lists only the clinical words;
identifiers are handled by the detectors.

Curated by hand for this project from standard clinical terminology; no
phrase is taken from any single source. Used exactly like lexicon_neuro.py:
words with a capital are clinical only beside a clinical word, acronyms are
gate-only, and every word passes the name guard.
"""
from __future__ import annotations

CARE_PHRASES: dict[str, tuple[str, ...]] = {
    "visit types and settings": (
        "chief complaint", "history of present illness", "review of systems", "past medical history",
        "past surgical history", "family history", "social history", "medication reconciliation",
        "admission history and physical", "interval history", "hospital course", "observation status",
        "inpatient admission", "emergency department visit", "triage acuity", "Emergency Severity Index",
        "ESI level 2", "fast track", "urgent care visit", "walk-in clinic", "retail clinic",
        "telehealth visit", "video visit", "audio-only visit", "patient located at home",
        "provider located at clinic", "verbal consent for telehealth", "e-visit", "patient portal message",
        "nurse triage line", "after-hours call", "outpatient surgery center", "ambulatory surgery center",
        "infusion center", "dialysis unit", "hemodialysis", "peritoneal dialysis", "home health",
        "home infusion", "skilled nursing facility", "long-term acute care hospital",
        "long-term care facility", "assisted living facility", "memory care unit", "nursing home",
        "inpatient rehabilitation facility", "acute rehab", "subacute rehab", "hospice care",
        "palliative care consult", "mobile clinic", "street medicine", "school-based health center",
        "student health", "occupational health", "employee health", "travel clinic",
        "public health department", "federally qualified health center", "free clinic",
        "correctional health", "Veterans Affairs", "military treatment facility", "place of service",
        "transfer center", "interfacility transfer", "air medical transport", "critical access hospital",
    ),
    "emergency and urgent": (
        "EMS run sheet", "prehospital", "field triage", "trauma activation", "primary survey",
        "secondary survey", "FAST exam", "eFAST", "log roll", "spinal motion restriction",
        "c-collar cleared", "door-to-needle", "door-to-groin", "stroke alert", "STEMI alert",
        "sepsis bundle", "code blue", "rapid response", "return precautions", "left without being seen",
        "against medical advice", "discharged home", "admitted to floor", "boarding in the ED",
        "suture repair", "laceration repair", "splint applied", "incision and drainage",
        "procedural sedation",
    ),
    "controlled substances and monitoring": (
        "CURES report reviewed", "PDMP query", "prescription drug monitoring program",
        "controlled substance agreement", "opioid treatment agreement", "pain agreement",
        "urine drug screen", "urine toxicology", "pill count", "morphine milligram equivalents",
        "MME per day", "naloxone prescribed", "Schedule II", "Schedule III", "Schedule IV",
        "early refill", "lost prescription", "aberrant behavior", "opioid use disorder",
        "buprenorphine-naloxone", "methadone maintenance", "medication-assisted treatment",
        "DEA registration", "e-prescribing of controlled substances", "taper plan",
        "oxycodone", "hydromorphone", "fentanyl patch", "tramadol", "tapentadol", "morphine ER",
        "alprazolam", "clonazepam", "lorazepam", "zolpidem", "amphetamine salts", "methylphenidate",
        "lisdexamfetamine", "testosterone cypionate", "pregabalin", "cannabidiol", "medical cannabis",
    ),
    "psychiatric care": (
        "mental status examination", "psychiatric emergency", "involuntary hold", "5150 hold",
        "72-hour hold", "conservatorship", "grave disability", "danger to self", "danger to others",
        "suicide risk assessment", "Columbia Suicide Severity Rating Scale", "safety plan",
        "inpatient psychiatric unit", "partial hospitalization program", "intensive outpatient program",
        "electroconvulsive therapy", "transcranial magnetic stimulation", "cognitive behavioral therapy",
        "dialectical behavior therapy", "PHQ-9", "GAD-7", "AUDIT-C", "CIWA-Ar", "COWS score",
        "sertraline", "escitalopram", "fluoxetine", "bupropion", "mirtazapine", "venlafaxine",
        "aripiprazole", "quetiapine", "olanzapine", "risperidone", "haloperidol", "clozapine",
        "lithium level", "lamotrigine", "divalproex", "lurasidone", "paliperidone palmitate",
        "long-acting injectable",
    ),
    "paediatric, foster and neonatal": (
        "well-child visit", "growth chart", "developmental milestones", "Ages and Stages Questionnaire",
        "M-CHAT", "newborn screen", "hearing screen", "bilirubin", "phototherapy", "Apgar score",
        "gestational age at birth", "small for gestational age", "NICU admission", "CPAP",
        "high-flow nasal cannula", "kangaroo care", "donor milk", "retinopathy of prematurity",
        "bronchopulmonary dysplasia", "necrotizing enterocolitis", "foster care placement",
        "foster parent", "kinship care", "child protective services", "caseworker",
        "court-ordered visitation", "guardian ad litem", "medical consent from agency",
        "school nurse", "individualized education program", "504 plan", "sports physical",
        "vision screening", "lead screening", "fluoride varnish",
    ),
    "vaccines and immunisation records": (
        "immunization record", "vaccine administered", "Vaccine Information Statement",
        "lot number", "expiration date", "injection site", "left deltoid", "vastus lateralis",
        "intramuscular", "subcutaneous", "Tdap", "DTaP", "MMR", "varicella vaccine", "IPV",
        "Hib vaccine", "hepatitis B vaccine", "hepatitis A vaccine", "HPV vaccine",
        "pneumococcal conjugate vaccine", "PCV20", "PPSV23", "meningococcal vaccine", "MenACWY",
        "MenB", "rotavirus vaccine", "influenza vaccine", "COVID-19 vaccine", "RSV vaccine",
        "nirsevimab", "zoster vaccine", "Shingrix", "tetanus immune globulin", "rabies vaccine",
        "BCG vaccine", "catch-up schedule", "vaccine declination", "VAERS report",
        "immunization registry", "state immunization information system",
    ),
    "complementary and traditional medicine": (
        "homeopathic remedy", "Arnica montana", "Nux vomica", "Belladonna 30C", "Oscillococcinum",
        "naturopathic", "acupuncture", "acupressure", "chiropractic adjustment", "osteopathic manipulative treatment",
        "massage therapy", "cupping", "moxibustion", "Ayurvedic", "traditional Chinese medicine",
        "herbal supplement", "turmeric", "ashwagandha", "kratom", "St. John's wort", "ginkgo biloba",
        "echinacea", "fish oil", "melatonin", "probiotic", "vitamin D3", "CBD oil", "reiki",
        "yoga therapy", "meditation", "biofeedback", "faith healer", "folk remedy",
    ),
    "pain management": (
        "chronic pain", "pain score 7/10", "numeric rating scale", "visual analog scale",
        "neuropathic pain", "complex regional pain syndrome", "trigger point injection",
        "genicular nerve block", "intra-articular injection", "nerve ablation",
        "multimodal analgesia", "nonopioid analgesics", "acetaminophen", "ibuprofen", "naproxen",
        "meloxicam", "celecoxib", "lidocaine patch", "topical diclofenac", "functional goals",
    ),
    "critical care and end of life": (
        "mechanical ventilation", "ventilator settings", "PEEP", "FiO2", "tidal volume",
        "spontaneous breathing trial", "extubated", "reintubated", "tracheostomy collar",
        "vasopressors", "norepinephrine", "vasopressin", "central line", "arterial line",
        "sedation vacation", "RASS score", "CAM-ICU", "delirium", "ECMO", "CRRT",
        "persistent vegetative state", "unresponsive wakefulness syndrome", "minimally conscious state",
        "locked-in syndrome", "coma", "brain death", "quadriplegia", "paraplegia", "tetraplegia",
        "do not resuscitate", "do not intubate", "full code", "comfort care", "withdrawal of care",
        "terminal extubation", "goals of care discussion", "advance directive", "health care proxy",
        "durable power of attorney for health care", "surrogate decision maker", "POLST form",
        "MOLST form", "time of death", "pronounced dead", "cause of death", "autopsy declined",
        "medical examiner", "coroner", "organ procurement organization", "tissue donation",
        "bereavement", "post-mortem care",
    ),
    "older adults and long-term care": (
        "activities of daily living", "instrumental activities of daily living", "fall precautions",
        "bed alarm", "pressure injury", "stage 2 pressure injury", "Braden scale", "Morse Fall Scale",
        "Mini-Cog", "MoCA", "SLUMS", "dementia with behavioral disturbance", "sundowning",
        "minimum data set", "care plan meeting", "restorative nursing", "wander guard",
        "hospice eligibility", "functional decline", "failure to thrive", "frailty",
    ),
    "sports medicine": (
        "athletic trainer", "return to play", "graduated return to play", "concussion protocol",
        "SCAT5", "ImPACT testing", "sideline assessment", "anterior cruciate ligament", "ACL reconstruction",
        "rotator cuff", "stress fracture", "pre-participation physical", "cleared for full contact",
        "heat illness", "exertional rhabdomyolysis",
    ),
    "public health and screening": (
        "tuberculosis screening", "QuantiFERON", "PPD placed", "PPD read", "contact tracing",
        "reportable disease", "health department notified", "STI screening", "HIV screening",
        "hepatitis C screening", "lead level", "newborn hearing screen", "colorectal cancer screening",
        "FIT test", "mammogram", "Pap smear", "low-dose CT", "blood pressure screening",
        "vision and hearing screening", "community health worker", "outreach",
    ),
    "records from elsewhere": (
        "outside records", "records from abroad", "translated records", "certified translation",
        "foreign medical records", "international patient", "medical tourism", "care received overseas",
        "outside hospital", "transferred from outside hospital", "care everywhere", "health information exchange",
        "records reviewed in Care Everywhere", "images on CD", "CD uploaded to PACS",
    ),
    "measurement and shorthand": (
        "mmHg", "cmH2O", "mL/hr", "mL/kg/hr", "mcg/kg/min", "units/hr", "mEq/L", "mg/dL",
        "mmol/L", "g/dL", "cells/mcL", "IU/mL", "beats per minute", "breaths per minute",
        "oxygen saturation", "room air", "nasal cannula", "non-rebreather", "body mass index",
        "intake and output", "strict I&O", "nothing by mouth", "as needed", "every 4 hours",
        "twice daily", "at bedtime", "weight-bearing as tolerated", "non-weight-bearing",
    ),
    "referrals and escalation of care": (
        "referral placed", "referred to neurosurgery", "referred to neuro-oncology",
        "urgent referral", "routine referral", "e-consult", "curbside consult", "second opinion",
        "multidisciplinary tumor board", "tumor board recommendation", "spine conference",
        "neurovascular conference", "epilepsy surgery conference", "escalation of care",
        "transfer to a higher level of care", "tertiary referral center", "comprehensive stroke center",
        "level I trauma center", "accepting physician", "transfer accepted", "bed assignment",
        "warm handoff", "care coordination", "closed-loop referral", "return to referring provider",
        "shared care", "co-management", "hospitalist co-management", "consult signed off",
        "follow up with primary care", "patient navigator", "nurse navigator", "survivorship clinic",
    ),
    "insurance and payers": (
        "prior authorization", "authorization approved", "authorization denied", "peer-to-peer",
        "medical necessity", "letter of medical necessity", "appeal submitted", "denial overturned",
        "explanation of benefits", "out-of-network", "in-network", "deductible", "copay",
        "coinsurance", "out-of-pocket maximum", "HMO", "PPO", "EPO", "POS plan", "high-deductible health plan",
        "Medicare Advantage", "Medicare Part A", "Medicare Part B", "Medicare Part D",
        "traditional Medicare", "Medicaid managed care", "dual eligible", "CHIP", "TRICARE",
        "Veterans Affairs benefits", "workers' compensation carrier", "auto insurance", "no-fault insurance",
        "personal injury protection", "uninsured", "self-pay", "charity care", "financial counselor",
        "coordination of benefits", "primary insurance", "secondary insurance", "formulary exception",
        "step therapy", "specialty pharmacy", "biosimilar substitution", "utilization management",
        "observation versus inpatient", "two-midnight rule", "case rate", "DRG", "bundled payment",
        "Blue Cross Blue Shield", "Kaiser Permanente", "UnitedHealthcare", "Aetna", "Cigna",
        "Humana", "Anthem", "Medicare", "Medicaid",
    ),
    "dentistry and oral and maxillofacial surgery": (
        "dental exam", "periodontal charting", "probing depths", "bleeding on probing",
        "gingivitis", "periodontitis", "scaling and root planing", "prophylaxis", "fluoride treatment",
        "dental caries", "Class II caries", "composite restoration", "amalgam restoration",
        "root canal therapy", "endodontic treatment", "pulpotomy", "pulpectomy", "crown preparation",
        "porcelain-fused-to-metal crown", "zirconia crown", "fixed partial denture", "complete denture",
        "dental implant", "implant abutment", "bone graft", "sinus lift", "ridge augmentation",
        "extraction", "surgical extraction", "third molar extraction", "impacted third molar",
        "dry socket", "alveolar osteitis", "bitewing radiographs", "periapical radiograph",
        "panoramic radiograph", "cone beam CT", "malocclusion", "Angle Class II", "orthodontic brackets",
        "clear aligners", "temporomandibular joint", "TMJ arthrocentesis", "bruxism", "night guard",
        "local anesthetic", "inferior alveolar nerve block", "articaine", "nitrous oxide sedation",
        "orthognathic surgery", "Le Fort I osteotomy", "bilateral sagittal split osteotomy",
        "genioplasty", "mandible fracture", "zygomaticomaxillary complex fracture", "orbital floor fracture",
        "naso-orbito-ethmoid fracture", "Le Fort fracture", "maxillomandibular fixation",
        "open reduction and internal fixation", "arch bars", "cleft lip and palate", "alveolar bone graft",
        "osteoradionecrosis", "medication-related osteonecrosis of the jaw", "oral cancer",
        "squamous cell carcinoma of the tongue", "neck dissection", "fibula free flap", "trismus",
        "odontogenic infection", "Ludwig angina", "tooth #14", "tooth 30", "Universal Numbering System",
    ),
    "skull base and neurosurgical subspecialties": (
        "skull base surgery", "anterior skull base", "middle cranial fossa", "posterior fossa",
        "petroclival meningioma", "clival chordoma", "olfactory groove meningioma", "esthesioneuroblastoma",
        "juvenile nasopharyngeal angiofibroma", "jugular foramen", "glomus jugulare", "paraganglioma",
        "endoscopic transclival approach", "transpetrosal approach", "Kawase approach",
        "cerebrovascular neurosurgery", "endovascular neurosurgery", "neurointerventional surgery",
        "neuro-oncology", "stereotactic and functional neurosurgery", "epilepsy surgery",
        "pediatric neurosurgery", "spine surgery", "complex spine", "minimally invasive spine surgery",
        "peripheral nerve surgery", "nerve transfer", "neurotrauma", "neurocritical care",
        "radiosurgery", "pain neurosurgery", "dorsal root entry zone lesioning", "cordotomy",
        "neuroendoscopy", "spinal oncology", "separation surgery", "en bloc spondylectomy",
        "spinal cord tumor", "intramedullary tumor", "intradural extramedullary", "spinal AVM",
        "spinal dural arteriovenous fistula", "Bilsky grade", "SINS score", "Tokuhashi score",
    ),
}

CARE_PRODUCTS: frozenset[str] = frozenset("""
    shingrix oscillococcinum quantiferon narcan suboxone sublocade vivitrol ayurvedic ayurveda
    unitedhealthcare aetna cigna humana anthem medicare medicaid tricare zirconia articaine
""".split())

CARE_ACRONYMS: frozenset[str] = frozenset("""
    ESI EMS FAST eFAST STEMI AMA LWBS PDMP CURES MME UDS MAT OUD DEA EPCS IOP PHP ECT TMS CBT
    DBT LAI ASQ NICU CPAP HFNC ROP BPD NEC CPS IEP VIS VAERS IIS DTaP Tdap MMR IPV Hib HPV
    PCV PPSV MenACWY MenB RSV BCG OMT TCM CRPS NRS VAS PEEP FiO2 SBT RASS ECMO CRRT DNR DNI
    POLST MOLST DPOA ADL IADL MDS SNF LTACH ALF FQHC ME OPO SCAT ACL PPD STI FIT LDCT CHW HIE
    NPO PRN QHS WBAT NWB
""".split())
