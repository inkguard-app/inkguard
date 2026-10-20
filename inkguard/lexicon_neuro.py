"""
NEUROSURGICAL CARE VOCABULARY.

The terms of neurosurgery and of the specialties around a neurosurgical
patient: neuroanaesthesia and intraoperative monitoring, neurocritical
care, neurology, neuroradiology and spine imaging, orthopaedic spine,
pain medicine and rehabilitation (PM&R), skull-base ENT, plastic and
reconstructive surgery, paediatric neurosurgery, obstetric and older-adult
care as they meet the nervous system, and the emergency, internal and
family medicine notes that come before and after an operation.

Why it exists: keeping clinical detail is why this tool was built. A
de-identification tool that removes "Spetzler-Martin", "Mayfield", "Kerrison"
or "Codman" as if they were people destroys the data a study needs, and
nobody notices, because a removed word leaves no trace.

How it is used (inkguard/clinical_lexicon.py): every word passes the same name
guard as the rest of the vocabulary. A word that is also someone's name
(Mayfield, Kerrison, Penfield, Leksell, Codman) is never protected on its
own: it is clinical only next to a clinical word ("Mayfield head holder",
"Penfield 4 dissector", "Codman valve"), and a person wearing that name is
still removed ("Dr. Mayfield", "Mr. Penfield").

Curated by hand for this project from standard clinical terminology,
covering the specialties and teams that write a neurosurgical patient's
record; no phrase is taken from any single source. Company and product
names are clinical content (what was implanted, which system navigated the
case); the people who represent those companies are identifiers, and remain
so. The independent test set in
benchmark/neuro/ was written separately and does not draw on this file.
"""
from __future__ import annotations

# Each group is phrases as clinicians write them. Their words are added to
# the clinical vocabulary; their two-word runs to the phrase list.
NEURO_PHRASES: dict[str, tuple[str, ...]] = {
    "cranial approaches and operations": (
        "pterional craniotomy", "orbitozygomatic approach", "frontotemporal craniotomy",
        "bifrontal craniotomy", "retrosigmoid approach", "far lateral approach",
        "suboccipital craniectomy", "translabyrinthine approach", "middle fossa approach",
        "interhemispheric approach", "transcallosal approach", "transsylvian dissection",
        "supracerebellar infratentorial approach", "telovelar approach", "endoscopic endonasal approach",
        "transsphenoidal resection", "extended endonasal approach", "keyhole craniotomy",
        "eyebrow craniotomy", "awake craniotomy", "decompressive hemicraniectomy",
        "decompressive craniectomy", "cranioplasty", "burr hole", "twist drill craniostomy",
        "subdural evacuating port system", "middle meningeal artery embolization",
        "microvascular decompression", "lumbar drain", "external ventricular drain",
        "ventriculostomy", "ventriculoperitoneal shunt", "ventriculoatrial shunt",
        "ventriculopleural shunt", "lumboperitoneal shunt", "endoscopic third ventriculostomy",
        "choroid plexus cauterization", "septostomy", "shunt revision", "shunt tap",
        "Ommaya reservoir", "Rickham reservoir", "stereotactic biopsy", "laser interstitial thermal therapy",
        "gross total resection", "subtotal resection", "near total resection", "supramaximal resection",
        "lobectomy", "amygdalohippocampectomy", "corpus callosotomy", "hemispherotomy",
        "multiple subpial transections", "temporal lobectomy", "lesionectomy",
    ),
    "neurovascular and interventional": (
        "aneurysm clipping", "aneurysm coiling", "stent-assisted coiling", "balloon-assisted coiling",
        "flow diversion", "Pipeline Embolization Device", "Pipeline Flex", "Surpass Streamline",
        "Woven EndoBridge", "WEB device", "FRED flow diverter", "Onyx embolization",
        "n-BCA embolization", "PHIL embolic", "Solitaire stent retriever", "Trevo retriever",
        "Penumbra aspiration", "ADAPT technique", "mechanical thrombectomy", "carotid endarterectomy",
        "carotid stenting", "EC-IC bypass", "STA-MCA bypass", "encephaloduroarteriosynangiosis",
        "pial synangiosis", "Spetzler-Martin grade", "Spetzler-Ponce class", "Hunt and Hess grade",
        "Hunt-Hess grade", "Fisher grade", "modified Fisher scale", "WFNS grade", "Borden type",
        "Cognard type", "Lindegaard ratio", "TICI 2b", "TICI 3", "mTICI", "ASPECTS score",
        "Raymond-Roy class", "Suzuki stage", "moyamoya disease", "cavernous malformation",
        "arteriovenous malformation", "dural arteriovenous fistula", "carotid-cavernous fistula",
        "vein of Galen malformation", "circle of Willis", "anterior communicating artery",
        "posterior communicating artery", "basilar tip", "PICA aneurysm", "vasospasm",
        "delayed cerebral ischemia", "triple-H therapy", "nimodipine",
    ),
    "tumour and neuro-oncology": (
        "glioblastoma", "IDH-mutant astrocytoma", "IDH-wildtype", "oligodendroglioma",
        "1p/19q codeletion", "MGMT promoter methylation", "H3 K27M", "diffuse midline glioma",
        "ependymoma", "medulloblastoma", "pilocytic astrocytoma", "craniopharyngioma",
        "meningioma", "Simpson grade", "WHO grade", "pituitary adenoma", "pituitary macroadenoma",
        "Knosp grade", "Hardy classification", "vestibular schwannoma", "Koos grade",
        "House-Brackmann grade", "chordoma", "chondrosarcoma", "hemangioblastoma",
        "von Hippel-Lindau", "brain metastases", "leptomeningeal disease", "Stupp protocol",
        "temozolomide", "bevacizumab", "Tumor Treating Fields", "Optune", "Karnofsky Performance Status",
        "Gamma Knife radiosurgery", "Leksell Gamma Knife", "Leksell frame", "CyberKnife",
        "stereotactic radiosurgery", "fractionated radiotherapy", "5-ALA fluorescence",
        "Gleolan", "fluorescein", "intraoperative MRI", "RANO criteria",
    ),
    "trauma and neurocritical care": (
        "Glasgow Coma Scale", "GCS 3T", "FOUR score", "Marshall classification", "Rotterdam CT score",
        "epidural hematoma", "acute subdural hematoma", "chronic subdural hematoma",
        "traumatic subarachnoid hemorrhage", "diffuse axonal injury", "contusion", "midline shift",
        "uncal herniation", "subfalcine herniation", "Cushing triad", "Cushing reflex",
        "intracranial pressure", "cerebral perfusion pressure", "ICP monitor", "Camino bolt",
        "Licox monitor", "brain tissue oxygen", "hyperosmolar therapy", "hypertonic saline",
        "mannitol", "pentobarbital coma", "targeted temperature management", "external ventricular drain",
        "EVD clamped", "EVD open at 10 cmH2O", "Brain Trauma Foundation guidelines",
        "brain death examination", "apnea test", "cervical collar", "Aspen collar",
        "Miami J collar", "halo vest", "Gardner-Wells tongs", "Philadelphia collar",
        "Canadian C-spine rule", "NEXUS criteria",
    ),
    "spine": (
        "anterior cervical discectomy and fusion", "ACDF", "cervical disc arthroplasty",
        "posterior cervical fusion", "laminoplasty", "laminectomy", "laminotomy", "foraminotomy",
        "microdiscectomy", "hemilaminectomy", "TLIF", "PLIF", "ALIF", "XLIF", "LLIF", "OLIF",
        "transforaminal lumbar interbody fusion", "pedicle screw", "lateral mass screw",
        "cortical bone trajectory", "interbody cage", "PEEK cage", "titanium cage",
        "rod and screw construct", "iliac bolt", "S2-alar-iliac screw", "kyphoplasty",
        "vertebroplasty", "odontoid screw", "C1-C2 fusion", "Harms technique", "Magerl technique",
        "occipitocervical fusion", "Smith-Robinson approach", "Wiltse approach",
        "pars defect", "spondylolisthesis", "Meyerding grade", "Cobb angle", "sagittal vertical axis",
        "pelvic incidence", "lumbar lordosis", "scoliosis", "adolescent idiopathic scoliosis",
        "Lenke classification", "Schwab classification", "TLICS score", "SLIC score",
        "AOSpine classification", "Denis classification", "Chance fracture", "burst fracture",
        "jumped facet", "hangman's fracture", "Jefferson fracture", "odontoid fracture",
        "Anderson and D'Alonzo type", "cauda equina syndrome", "conus medullaris",
        "central cord syndrome", "Brown-Sequard syndrome", "anterior cord syndrome",
        "ASIA Impairment Scale", "ASIA A", "Frankel grade", "Nurick grade", "mJOA score",
        "Oswestry Disability Index", "Neck Disability Index", "McCormick grade",
        "Modic changes", "Pfirrmann grade", "Schizas grade", "flexion-extension radiographs",
        "EOS imaging", "Jackson table", "Wilson frame", "Mayfield head holder", "Mayfield pins",
        "Kerrison rongeur", "pituitary rongeur", "Leksell rongeur", "Penfield 4 dissector",
        "Woodson elevator", "Cobb elevator", "Midas Rex drill", "high-speed drill",
        "matchstick bur", "diamond bur", "Gelfoam", "DuraSeal", "DuraGen", "Tisseel",
        "bone wax", "allograft", "autograft", "demineralized bone matrix", "Infuse bone graft",
        "rhBMP-2", "O-arm", "StealthStation", "Mazor X", "ExcelsiusGPS", "ROSA robot",
        "Brainlab Curve", "Kick navigation", "fluoroscopy", "C-arm", "Jamshidi needle",
    ),
    "functional and epilepsy": (
        "deep brain stimulation", "subthalamic nucleus", "globus pallidus interna", "ventral intermediate nucleus",
        "microelectrode recording", "Medtronic Percept", "Boston Scientific Vercise", "Abbott Infinity",
        "implantable pulse generator", "directional lead", "responsive neurostimulation",
        "NeuroPace RNS", "vagus nerve stimulator", "LivaNova", "stereoelectroencephalography", "SEEG",
        "subdural grid", "Wada test", "Engel class", "Engel class IA", "ILAE class",
        "focal impaired awareness seizure", "generalized tonic-clonic seizure", "status epilepticus",
        "Todd paralysis", "UPDRS", "Hoehn and Yahr stage", "essential tremor", "dystonia",
        "trigeminal neuralgia", "glycerol rhizotomy", "balloon compression", "spasticity",
        "selective dorsal rhizotomy", "intrathecal baclofen pump", "Modified Ashworth Scale",
        "focused ultrasound thalamotomy", "Visualase", "NeuroBlate",
    ),
    "hydrocephalus and paediatric": (
        "normal pressure hydrocephalus", "Hakim triad", "Evans index", "tap test",
        "Codman Hakim programmable valve", "Codman Certas Plus", "Medtronic Strata valve",
        "Aesculap proGAV", "Miethke shunt", "Delta valve", "antisiphon device", "slit ventricle syndrome",
        "shunt series", "shunt malfunction", "Chiari malformation", "Chiari I malformation",
        "Chiari decompression", "syringomyelia", "Dandy-Walker malformation", "myelomeningocele",
        "tethered cord", "lipomyelomeningocele", "craniosynostosis", "sagittal synostosis",
        "metopic synostosis", "cranial vault remodeling", "helmet therapy", "plagiocephaly",
        "intraventricular hemorrhage", "Papile grade", "germinal matrix", "fontanelle",
        "head circumference", "Pierre Robin sequence", "Crouzon syndrome", "Apert syndrome",
        "neurofibromatosis type 1", "tuberous sclerosis", "Sturge-Weber syndrome",
    ),
    "neurology": (
        "NIH Stroke Scale", "NIHSS", "modified Rankin Scale", "tenecteplase", "alteplase",
        "last known well", "large vessel occlusion", "transient ischemic attack", "ABCD2 score",
        "multiple sclerosis", "McDonald criteria", "EDSS", "ocrelizumab", "natalizumab",
        "myasthenia gravis", "acetylcholine receptor antibody", "Guillain-Barre syndrome",
        "Miller Fisher variant", "CIDP", "amyotrophic lateral sclerosis", "Parkinson disease",
        "carbidopa-levodopa", "Lewy body dementia", "Huntington disease", "Bell palsy",
        "Ramsay Hunt syndrome", "Horner syndrome", "Wallenberg syndrome", "Weber syndrome",
        "Parinaud syndrome", "Gerstmann syndrome", "Babinski sign", "Hoffmann sign",
        "Romberg sign", "Lhermitte sign", "Spurling test", "Tinel sign", "Phalen test",
        "pronator drift", "dysdiadochokinesia", "internuclear ophthalmoplegia", "papilledema",
        "Frisen grade", "levetiracetam", "lacosamide", "valproate", "fosphenytoin", "phenobarbital",
        "continuous EEG", "lateralized periodic discharges", "burst suppression",
        "electromyography", "nerve conduction study", "Wernicke aphasia", "Broca aphasia",
    ),
    "neuroradiology and spine imaging": (
        "T1-weighted", "T2-weighted", "FLAIR", "diffusion-weighted imaging", "ADC map",
        "susceptibility-weighted imaging", "gradient echo", "post-gadolinium", "MR perfusion",
        "MR spectroscopy", "DTI tractography", "functional MRI", "CT angiography", "CT perfusion",
        "CT venography", "digital subtraction angiography", "myelogram", "CT myelogram",
        "MRI with and without contrast", "motion artifact", "repeat sequence due to motion",
        "sedated MRI", "outside images on CD", "uploaded to PACS", "Hounsfield units",
        "restricted diffusion", "ring enhancement", "vasogenic edema", "mass effect",
        "transependymal flow", "periventricular hyperintensities", "Fazekas grade",
        "Sylvian fissure", "foramen of Monro", "aqueduct of Sylvius", "fourth ventricle",
        "foramen magnum", "cerebellopontine angle", "Meckel cave", "cavernous sinus",
        "sella turcica", "clivus", "basilar invagination", "McRae line", "Chamberlain line",
        "Wackenheim line", "atlantodental interval", "neural foraminal stenosis",
        "lateral recess stenosis", "ligamentum flavum hypertrophy", "facet arthropathy",
        "disc osteophyte complex", "annular fissure", "disc extrusion", "disc protrusion",
        "sequestered fragment", "OPLL", "DISH", "STIR hyperintensity", "bone marrow edema",
        "DEXA scan", "T-score", "Z-score",
    ),
    "neuroanaesthesia and monitoring": (
        "total intravenous anesthesia", "TIVA", "propofol infusion", "remifentanil infusion",
        "dexmedetomidine", "sevoflurane", "desflurane", "awake fiberoptic intubation",
        "rapid sequence intubation", "arterial line", "central venous catheter",
        "precordial Doppler", "venous air embolism", "sitting position", "park bench position",
        "prone position", "pin site block", "scalp block", "intraoperative neuromonitoring",
        "IONM", "somatosensory evoked potentials", "SSEP", "motor evoked potentials", "MEP",
        "transcranial MEP", "free-run EMG", "triggered EMG", "D-wave", "BAER", "BAEP",
        "train-of-four", "baseline signals", "loss of signals", "wake-up test",
        "tranexamic acid", "cell saver", "ASA class", "Mallampati class", "MAC anesthesia",
    ),
    "pain medicine and rehabilitation": (
        "epidural steroid injection", "transforaminal epidural", "interlaminar epidural",
        "medial branch block", "radiofrequency ablation", "sacroiliac joint injection",
        "facet joint injection", "selective nerve root block", "stellate ganglion block",
        "occipital nerve block", "spinal cord stimulator", "SCS trial", "dorsal root ganglion stimulation",
        "Nevro HF10", "Abbott Proclaim", "Boston Scientific WaveWriter", "intrathecal pump",
        "Medtronic SynchroMed", "ziconotide", "gabapentin", "pregabalin", "duloxetine",
        "physical therapy", "occupational therapy", "speech-language pathology",
        "inpatient rehabilitation facility", "FIM score", "Berg Balance Scale",
        "Timed Up and Go", "functional capacity evaluation", "maximum medical improvement",
        "impairment rating", "work restrictions", "light duty", "TENS unit", "lumbar brace",
        "LSO brace", "TLSO brace", "CTO brace",
    ),
    "instruments, devices and signs (2)": (
        "Zeiss Kinevo microscope", "Zeiss Pentero", "Leica microscope", "Karl Storz endoscope",
        "Hopkins rod endoscope", "Ponte osteotomy", "Smith-Petersen osteotomy",
        "pedicle subtraction osteotomy", "Humphrey visual field", "Goldmann perimetry",
        "Hollenhorst plaque", "Marcus Gunn pupil", "VerifyNow P2Y12", "Phenom microcatheter",
        "Headway microcatheter", "Echelon microcatheter", "Sofia catheter", "Angio-Seal closure",
        "Copernic RC balloon", "Ghajar guide", "AutoStim", "Risser grade", "Glasgow Outcome Scale",
        "Glasgow Outcome Scale-Extended", "longus colli", "scoliometer", "heparinised",
        "heparinized", "normocapnia", "hypocapnia", "hypercapnia", "upgoing plantar",
        "sellar region", "suprasellar", "optico-carotid recess", "opticocarotid recess",
        "METRx tubular retractor", "Medtronic METRx", "Sofia aspiration catheter",
        "Doppler ultrasound", "transcranial Doppler", "Holter monitoring", "stump pressure",
        "hyperdense MCA sign", "haemorrhagic transformation", "hemorrhagic transformation",
        "quadrantanopia", "double simultaneous stimulation", "dysphagia screen",
    ),
    "skull base ENT and plastics": (
        "nasoseptal flap", "Hadad-Bassagasteguy flap", "beta-2 transferrin", "CSF rhinorrhea",
        "CSF otorrhea", "lumbar drain", "fat graft", "fascia lata graft", "pericranial flap",
        "temporalis muscle flap", "cochlear implant", "facial nerve monitoring", "acoustic reflex",
        "audiogram", "word recognition score", "vestibular testing", "videonystagmography",
        "septoplasty", "middle turbinate", "sphenoid sinus", "tracheostomy", "PEG tube",
        "PEEK cranioplasty", "titanium mesh cranioplasty", "patient-specific implant",
        "autologous bone flap", "bone flap stored in abdominal pocket", "rotation flap",
        "advancement flap", "free flap", "anterolateral thigh flap", "latissimus dorsi flap",
        "tissue expander", "split-thickness skin graft", "negative pressure wound therapy",
        "wound VAC", "scalp dehiscence",
    ),
    "obstetric and older adult": (
        "gravida", "para", "G2P1", "gestational age", "estimated date of delivery",
        "fetal heart tones", "non-stress test", "magnesium sulfate", "eclampsia",
        "posterior reversible encephalopathy syndrome", "PRES", "reversible cerebral vasoconstriction syndrome",
        "cerebral venous sinus thrombosis", "pituitary apoplexy", "Sheehan syndrome",
        "fetal monitoring", "abdominal shielding", "frailty index", "Clinical Frailty Scale",
        "fall risk", "polypharmacy", "anticoagulation reversal", "Kcentra", "andexanet alfa",
        "idarucizumab", "vitamin K", "platelet transfusion", "goals of care", "DNR/DNI",
        "POLST", "hospice",
    ),
    "billing and medico-legal": (
        "CPT code", "ICD-10 code", "modifier 59", "modifier 22", "prior authorization",
        "peer-to-peer review", "independent medical examination", "workers' compensation",
        "letter of protection", "personal injury", "motor vehicle collision", "restrained driver",
        "airbag deployment", "loss of consciousness", "mechanism of injury", "date of injury",
        "causation", "maximum medical improvement", "disability rating", "deposition",
        "records request", "release of information",
    ),
}

# Products and companies, clinical on their own; the name guard in
# clinical_lexicon.py still demotes any the name registry holds ("camino").
# A company or instrument named after a person (Codman, Leksell, Mayfield)
# is not here: it stays clinical only beside a clinical word.
NEURO_PRODUCTS: frozenset[str] = frozenset("""
    medtronic brainlab globus nuvasive penumbra integra aesculap miethke biomet synthes
    optune gleolan gelfoam duraseal duragen tisseel visualase neuroblate kcentra livanova
    excelsiusgps stealthstation nevro mazor synchromed vercise wavewriter percept certas
    progav licox camino trevo surpass infuse neuropace oarm o-arm cyberknife
    kinevo pentero verifynow metrx angio-seal copernic autostim visualase
""".split())

# Acronyms of the field. Gate-only (inkguard/agate.py): they stop the note being
# held for review, but they never shield a word from the name layers,
# because three capitals after "seen by" or a dash are someone's initials
# until a person says otherwise.
NEURO_ACRONYMS: frozenset[str] = frozenset("""
    AComm PComm ACoA PCoA AChA SCA PICA AICA MMA STA ECA ICA JOA mJOA cEEG vEEG EMU
    NIF ITB TCD TCDs CNIM ISNCSCI IPG UDI AAO-HNS ELAPSS SIBICC ULBD DBX iNPH NPH ICU NeuroICU
    NSICU DOC MCS UWS CRS-R GOS GOSE RTP cSDH aSDH MMAE
    MVC MVA TBI mTBI SCI GSW ATV LOC PTA SAH SDH EDH IPH IVH ICH DAI NPH ETV VPS EVD ICP CPP ACDF TLIF PLIF ALIF XLIF LLIF OLIF SCS DBS VNS RNS SEEG LITT SRS IONM SSEP MEP TIVA PACU NICU PICU SICU NSICU TICI mTICI ASPECTS NIHSS mRS GCS WFNS AVM DAVF CCF LVO TIA CVST PRES RCVS OPLL DISH STIR DEXA EDSS CIDP ALS FIM ODI NDI mJOA IME MMI LOP PI SVA PEEK TLSO LSO CTO ADC DWI SWI FLAIR CTA CTP CTV DSA MRA MRV PACS
""".split())

# Words that, after one of the ambiguous words above, make it clinical:
# "Mayfield head holder", "Codman valve", "Kerrison 3 mm", "Codman rep"
# (the company stays; the rep's own name is still an identifier).
NEURO_QUALIFIERS: frozenset[str] = frozenset({
    "holder", "pins", "frame", "table", "tongs", "dissector", "rongeur", "elevator",
    "drill", "bur", "burr", "microscope", "navigation", "system", "robot", "valve",
    "reservoir", "bolt", "monitor", "collar", "brace", "cage", "screw", "screws", "rod",
    "rods", "plate", "construct", "instrumentation", "implant", "stimulator", "lead",
    "generator", "device", "retriever", "coil", "coils", "malformation", "fissure",
    "aqueduct", "foramen", "cave", "triangle", "flap", "line", "grade", "class", "type",
    "triad", "reflex", "rep", "representative", "catalog", "model", "sequence", "technique",
    "approach", "protocol", "position", "classification", "variant", "embolization",
})


def all_phrases() -> dict[str, tuple[str, ...]]:
    """This file's groups and the care-settings groups (lexicon_care.py)."""
    from inkguard.lexicon_care import CARE_PHRASES
    return {**NEURO_PHRASES, **CARE_PHRASES}


def all_products() -> frozenset[str]:
    from inkguard.lexicon_care import CARE_PRODUCTS
    return NEURO_PRODUCTS | CARE_PRODUCTS


def all_acronyms() -> frozenset[str]:
    from inkguard.lexicon_care import CARE_ACRONYMS
    return NEURO_ACRONYMS | CARE_ACRONYMS


def words(*, proper: bool | None = None) -> frozenset[str]:
    """Every word of every phrase, lowercase, as the vocabulary holds them.
    proper=True: only words written with a capital (eponyms, companies,
    products: someone may carry the name); proper=False: only the others."""
    import re
    out = set()
    for group in all_phrases().values():
        for phrase in group:
            for raw in re.split(r"[\s/]+", phrase):
                w = raw.strip(".,;:()'\"").lower()
                # Acronyms ("MEP", "SCS", "mTICI") stay gate-only, like
                # NEURO_ACRONYMS: as words they would shield initials.
                core = raw.strip(".,;:()'\"")
                if len(core) <= 6 and sum(c.isupper() for c in core) >= 2:
                    continue
                if proper is not None and (core[:1].isupper()) != proper:
                    continue
                if w and not w.isdigit():
                    out.add(w)
                    if "-" in w:
                        out.update(p for p in w.split("-") if p and not p.isdigit())
    return frozenset(out)


def bigrams() -> frozenset[str]:
    """Two-word runs of the phrases, for the phrase check."""
    import re
    out = set()
    for group in all_phrases().values():
        for phrase in group:
            toks = [t for t in re.split(r"\s+", phrase.lower()) if t]
            out.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    return frozenset(out)
