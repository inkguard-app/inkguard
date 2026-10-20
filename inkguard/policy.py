"""
Policy engine: turns detections into replacements, then checks the result.

- Every detection of a type the profile removes is replaced, whatever its
  score.
- Person candidates the evidence scorer held back are replaced with
  [NAME] when they score at or above the exit floor (0.30) in a section
  where names appear, or in any section of a note kind that names many
  people; a word no vocabulary knows is replaced at a lower score. Held
  candidates outside these limits are left in the text and the final word
  check decides whether the note is flagged.
- The final word check (agate) flags the note for review if any word left
  in it is one the tool cannot name. It always runs.
- If the scanner fails, the note is not processed (BLOCKED). There is no
  setting that passes a note through unchanged.
"""
import os
import re
from dataclasses import dataclass
from enum import Enum

from inkguard.config import settings
from inkguard.phi_scanner import scan, scan_with_trace, PHIHit
from inkguard.clinical_context import verify, IntegrityReport
from inkguard import hipaa
from inkguard import basalt
from inkguard import contamination as contam
from inkguard import linkage
from inkguard import date_shift
from inkguard import deident_profile

from inkguard import talc
from inkguard import obsidian
from inkguard import onyx as pseudo


class Action(str, Enum):
    ALLOW = "allow"           # no PHI found
    REDACTED = "redacted"     # PHI found and masked, redacted note written
    BLOCKED = "blocked"       # scanner failure under fail-closed policy
    # Redacted, but flagged: a word left in the draft is one the tool
    # cannot name (agate). A person must look at the listed words before
    # the draft is used. See inkguard/agate.py.
    REVIEW = "review"


@dataclass
class ScanResult:
    action: Action
    clean_text: str
    original_text: str
    hits: list[PHIHit]
    error: str | None = None
    integrity: IntegrityReport | None = None
    diagnosis: object | None = None  # basalt.Diagnosis
    contamination: object | None = None  # contamination.ContaminationReport
    linkage: object | None = None        # linkage.LinkageReport
    profile: object | None = None        # deident_profile.ProfileConfig
    date_shift_report: object | None = None  # date_shift.ShiftReport
    exit_report: object | None = None        # obsidian.ExitReport
    trace: object | None = None              # phi_scanner.ScanTrace (no PHI)
    ledger: dict | None = None               # talc.Ledger summary
    oplog: dict | None = None                # oplog record, when requested
    pseudonyms: dict | None = None           # (start,end) -> Assignment, RAM only
    comprehension: object | None = None      # agate.Comprehension (offsets only)
    # Identifiers the profile KEEPS (Limited Data Set: dates, towns, ZIP),
    # after conflicts were settled. Shown to reviewers; never redacted.
    kept: list | None = None

    @property
    def cleared(self) -> bool:
        """True when the result has no flags (no person is asked to look
        at it first). A redacted note can still contain identifiers the
        tool missed."""
        return self.action in (Action.ALLOW, Action.REDACTED)


def derived_codes_allowed() -> bool:
    """Codes computed FROM an identifier (the legacy "hash" mode) are allowed
    only when the practice has recorded that an Expert Determination covers
    them: PHI_ALLOW_DERIVED_CODES=expert-determination. Safe Harbor output
    never uses them."""
    import os
    return os.getenv("PHI_ALLOW_DERIVED_CODES", "").strip().lower() == "expert-determination"


def _mask(entity_type: str, original: str, mode: str = "mask") -> str:
    # The mode is the one the CALLER asked for, never a process-wide
    # setting, so an explicit "mask" or "pseudonym" call never produces
    # name-derived codes.
    if mode == "hash":
        # KEYED. An unkeyed hash of an identifier can be reversed by
        # hashing every surname in the public census list and matching,
        # so the hash is keyed. The key comes from the same keystore as
        # the pseudonyms, under its own purpose label.
        import hmac, hashlib
        from inkguard.security import KeyStore
        key = KeyStore().derive("redaction-hash-v1")
        digest = hmac.new(key, original.strip().lower().encode(),
                          hashlib.sha256).hexdigest()[:8]
        return f"<{entity_type}_{digest}>"
    return talc.placeholder(entity_type)


_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _date_placeholder(original: str, now_year: int | None = None) -> str:
    """Safe Harbor removes every element of a date EXCEPT the year —
    unless the year points to an age over 89, which must be aggregated.
    So a date with a four-digit year becomes that year ("03/14/1958" ->
    "1958"); a year 90 or more years back, or a date with no four-digit
    year ("4/13", "3/14/58"), becomes [DATE]. Deterministic: the same
    input always gives the same output."""
    import datetime
    now_year = now_year or datetime.date.today().year
    m = _YEAR.search(original)
    if m and int(m.group(1)) > now_year - 90:
        return m.group(1)
    return "[DATE]"


def _replacement(hit, assignments, mode: str = "mask") -> str:
    if hit.layer == "uncertain":
        # The exit mask: the engine was unsure, so what leaves is an
        # ambiguous placeholder, never the token.
        return "[NAME]"
    if assignments is not None and hit.entity_type == "PERSON":
        a = assignments.get((hit.start, hit.end))
        if a is not None:
            return a.token
    # The placeholder uses the shared type registry (talc): "[DATE]", never a
    # recognizer's private subtype ("DATE_MD", "PHONE_INTL"). A
    # reader of the output — a person or a researcher's program — sees one name
    # per Safe Harbor category, whichever recognizer found it.
    canon = talc.canonical(hit.entity_type)
    if canon == "DATE_TIME" and mode != "hash":
        return _date_placeholder(hit.text)
    return _mask(canon, hit.text, mode)


def _kept_type_conflicts(text: str, retained, redactions, trace, confirmed: set,
                         removed) -> list[PHIHit]:
    """Removals forced by the Limited Data Set's kept types (see evaluate).

    A TOWN is kept only where the note plainly uses it as a place (a state
    or ZIP code after it, a street before it, "lives in" / "traveling from"
    before it) and no person cue touches it (agate._kept_town). "Call from Austin" is as likely a person, so it is removed
    as under Safe Harbor — the LDS permits keeping a town, never requires
    it. A kept town spelled like a name confirmed for this patient is
    removed too: keeping "Jackson" the town keeps "Jackson" the surname.

    A detection of a REMOVED type (a person, a record number) that a kept
    span swallowed during dedupe is removed on its own span — unless the
    span is a kept town and the person reading came only from a weak layer
    (a model or an unknown-word guess) and is no confirmed name."""
    from inkguard import agate
    targets = obsidian._name_targets(confirmed) if confirmed else set()

    def names_in(span: str) -> bool:
        return any(re.search(rf"(?<![\w'\-]){re.escape(t)}(?![\w'\-])", span) for t in targets)

    taken = [(h.start, h.end) for h in redactions]

    def free(a: int, b: int) -> bool:
        return not any(a < e and s < b for s, e in taken)

    out: list[PHIHit] = []
    kept_towns: list[PHIHit] = []
    for h in retained:
        if talc.canonical(h.entity_type) != "LOCATION" or h.entity_type == "ZIP_CODE":
            continue
        span = text[h.start:h.end]
        if re.fullmatch(agate._US_STATE, span.strip(" .,")):
            continue                                   # a state is never an identifier
        if (not names_in(span) and agate._kept_town(text, h.start, h.end)):
            kept_towns.append(h)
            continue
        if free(h.start, h.end):
            why = "kept_town_is_a_name" if names_in(span) else "town_without_place_evidence"
            out.append(PHIHit(h.entity_type, h.start, h.end, span, h.score,
                              "lds_conflict", getattr(h, "section", ""), (why,)))
            taken.append((h.start, h.end))

    if trace.ledger is not None:
        covered = set()
        for h in redactions + out:
            covered.update(range(h.start, h.end))
        for e in sorted(trace.ledger.entries.values(), key=lambda x: (x.start, -(x.end - x.start))):
            if e.fate or not removed(e.entity):
                continue
            if all(p in covered or text[p].isspace() for p in range(e.start, e.end)):
                continue
            over = [h for h in retained if h.start < e.end and e.start < h.end]
            if not over:
                continue
            if (talc.canonical(e.entity) == "PERSON" and e.layer in ("candidate", "ner", "ner_xx")
                    and any(h is k for h in over for k in kept_towns)
                    and not names_in(text[e.start:e.end])):
                e.fate, e.reason = talc.NOT_PHI, "kept town (Limited Data Set): place evidence"
                continue
            if free(e.start, e.end):
                out.append(PHIHit(e.entity, e.start, e.end, text[e.start:e.end], 0.5,
                                  "lds_conflict", "", ("kept_type_overlap",)))
                taken.append((e.start, e.end))
                covered.update(range(e.start, e.end))
    return out


_INITIAL_BEFORE = re.compile(r"(?<![\w.])([A-Z])\.[ \t]*$")
_INITIAL_AFTER = re.compile(r"^(,?[ \t]*)([A-Z])\.?(?=[\s,;:)\]]|$)")


def _role_before(text: str, pos: int) -> bool:
    """A role word ("Anaesthesia", "registrar", "surgeon") right before."""
    from inkguard.jasper import _ROLE
    return bool(re.search(rf"(?i:\b(?:{_ROLE}))[ \t]*[:,]?[ \t]*$", text[max(0, pos - 40):pos]))


def _absorb_initials(text: str, hits: list, *, extend: bool) -> list:
    """Take a single-letter initial written next to a removed person's name
    along with it. In pseudonym mode the initial becomes its own removal, so
    a code is never computed from a longer span."""
    import dataclasses
    taken = [(h.start, h.end) for h in hits]
    free = lambda a, b: not any(x < b and a < y for x, y in taken)      # noqa: E731
    out = []
    extra = []
    for h in hits:
        if talc.canonical(h.entity_type) != "PERSON":
            out.append(h)
            continue
        s0, e0 = h.start, h.end
        m = _INITIAL_BEFORE.search(text[max(0, s0 - 4):s0])
        if m:
            a = max(0, s0 - 4) + m.start(1)
            # Only where a name list or a sign-off puts it: after a line
            # start, punctuation, or a joining or role word. "Vitamin D.
            # Smith" keeps its vitamin.
            lead = text[max(0, a - 12):a]
            if not (re.search(r"(?:^|[\n:;,(/\-—–]|\b(?:and|by|with|to|from|per|cc|pt|patient|"
                              r"dr|mr|mrs|ms|mx|prof|rn|np|pa)\.?)[ \t]*$", lead, re.IGNORECASE)
                    or _role_before(text, a)):
                m = None
        if m:
            if free(a, a + 2):
                if extend:
                    s0 = a
                else:
                    extra.append(PHIHit("PERSON", a, a + 1, text[a], h.score, h.layer))
        m = _INITIAL_AFTER.match(text[e0:e0 + 6])
        if m and not text[e0:e0 + 6].startswith(("\n",)):
            a = e0 + m.start(2)
            b = e0 + m.end(2)
            if free(a, b):
                if extend:
                    e0 = b
                else:
                    extra.append(PHIHit("PERSON", a, b, text[a:b], h.score, h.layer))
        # A given name or surname standing right beside the removed name
        # ("Oona [Lachapelle]", "[SOLHEIM-BARZANI], INGRID") is the rest of
        # it, if it reads as a name and not as a word or a clinical term.
        from inkguard.jasper import _kin_token, _names as _registry

        def _namey(w: str) -> bool:
            # On a name list, and read as a name: "Ingrid", "Oona"; not
            # "Acct", "Monday" or a clinical word.
            return w.lower().strip("'’-") in _registry() and _kin_token(w)
        mb = re.search(r"([A-ZÀ-Þ][A-Za-zÀ-ɏ'’-]+)[ \t]+$", text[max(0, s0 - 30):s0])
        if mb:
            a = max(0, s0 - 30) + mb.start(1)
            if free(a, a + len(mb.group(1))) and _namey(mb.group(1)) \
                    and not _role_before(text, a + len(mb.group(1)) + 1):
                if extend:
                    s0 = a
                else:
                    extra.append(PHIHit("PERSON", a, a + len(mb.group(1)), mb.group(1), h.score, h.layer))
        ma = re.match(r"(,[ \t]*|[ \t]+)([A-ZÀ-Þ][A-Za-zÀ-ɏ'’-]+)(?![\w'’-])", text[e0:e0 + 32])
        if ma:
            a, b = e0 + ma.start(2), e0 + ma.end(2)
            if free(a, b) and _namey(ma.group(2)):
                if extend:
                    e0 = b
                else:
                    extra.append(PHIHit("PERSON", a, b, text[a:b], h.score, h.layer))
        if (s0, e0) != (h.start, h.end):
            h = dataclasses.replace(h, start=s0, end=e0, text=text[s0:e0])
        out.append(h)
    return out + extra


def _apply_redactions(text: str, hits: list[PHIHit], assignments=None,
                      keep_dates: bool = False, mode: str = "mask") -> str:
    """keep_dates: leave date spans in place for the date shifter, which
    runs next (see _shift_dates)."""
    # Apply back-to-front so earlier offsets stay valid as we mutate.
    for hit in sorted(hits, key=lambda h: h.start, reverse=True):
        if keep_dates and talc.canonical(hit.entity_type) == "DATE_TIME":
            continue
        text = text[:hit.start] + _replacement(hit, assignments, mode) + text[hit.end:]
    return text


def _shift_dates(clean: str, redactions, patient_key: str):
    """Shift every date by the patient's offset; mask whatever detected
    date the shifter could not parse. Fail closed: a detected date never
    leaves unshifted."""
    clean, report = date_shift.shift_text(
        clean, patient_key, max_offset_days=date_shift.max_offset())
    for h in redactions:
        if talc.canonical(h.entity_type) == "DATE_TIME" and h.text.strip() \
                and re.search(r"(?<![\w/.-])" + re.escape(h.text.strip()) + r"(?![\w/-])", clean):
            clean = re.sub(r"(?<![\w/.-])" + re.escape(h.text.strip()) + r"(?![\w/-])",
                           "[DATE]", clean)
    return clean, report


def _apply_safe_harbor_rules(text: str) -> tuple[str, int]:
    """Apply the two Safe Harbor transforms that are generalizations
    rather than deletions. Returns (new_text, changes_applied).

    Ages >= 90 become "90 or older" (45 CFR 164.514(b)(2)(i)(C)).
    Ages < 90 are left ALONE — they are legal to retain and are
    diagnostically load-bearing ("67-year-old male with crushing
    substernal chest pain" is a completely different clinical picture
    from "<AGE>-year-old male").

    ZIPs truncate to 3 digits, or 000 for HHS-restricted low-population
    prefixes.
    """
    changes = 0

    age_findings = [a for a in hipaa.find_ages(text) if a.is_identifier]
    for age in sorted(age_findings, key=lambda a: a.start, reverse=True):
        text = text[:age.start] + age.replacement + text[age.end:]
        changes += 1

    text, zip_changes = hipaa.generalize_zip(text)
    changes += zip_changes

    # Years that would give back an age over 89.
    text, year_changes = hipaa.guard_years_for_old_age(text)
    changes += year_changes

    return text, changes


_CHAIN_GAP = re.compile(r"[ \t]{1,2}|[ \t]?-[ \t]?")
_CHAIN_TOKEN = re.compile(r"[A-Z][A-Za-z'’-]*")


def _chain_remainder(text: str, start: int, end: int,
                     person_spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Parts of a held span that belong to a removed name.

    Overlap: the held span "Barrett Wrasman" overlaps the removed
    "Wrasman" -> return "Barrett". Adjacency: held "Mayberry" sits one
    space after the removed "Jianhai" -> return "Mayberry". Only
    capitalised tokens on the same line are returned."""
    out: list[tuple[int, int]] = []
    overlaps = [(a, b) for a, b in person_spans if a < end and start < b]
    if overlaps:
        for m in _CHAIN_TOKEN.finditer(text, start, end):
            if not any(m.start() < b and a < m.end() for a, b in overlaps):
                out.append((m.start(), m.end()))
        return out
    tok = text[start:end]
    if not _CHAIN_TOKEN.fullmatch(tok):
        return []
    for a, b in person_spans:
        if b <= start and _CHAIN_GAP.fullmatch(text[b:start]):
            return [(start, end)]
        if end <= a and _CHAIN_GAP.fullmatch(text[end:a]):
            return [(start, end)]
    return []


def evaluate(text: str, patient_key: str | None = None,
             profile_name: str | None = None, *,
             memory: set | None = None,
             redaction_mode: str | None = None,
             want_oplog: bool = False,
             file_context=None) -> ScanResult:
    """Scan, redact, verify on exit, and report.

    patient_key     Identifies the PATIENT (not the document). Keeps date
                    offsets and pseudonym codes consistent across all of a
                    patient's notes. Never stored raw.
    profile_name    "safe_harbor" (default) or "limited_data_set".
    memory          Names confirmed in the patient's OTHER notes (RAM only).
    redaction_mode  "mask" ([NAME]), "pseudonym" ([PERSON-K7Q3F]) or
                    "hash". Defaults to settings.redaction_mode.
    want_oplog      Build the private operation log (no note text).
    """
    config = deident_profile.get_profile(profile_name)
    is_lds = config.profile is deident_profile.Profile.LIMITED_DATA_SET
    mode = (redaction_mode or settings.redaction_mode or "mask").lower()
    if mode not in ("mask", "pseudonym", "hash"):
        return ScanResult(Action.BLOCKED, clean_text="", original_text=text, hits=[],
                          error=f"redaction_mode: unknown mode {mode!r}", profile=config)
    if mode == "hash" and not derived_codes_allowed():
        # "hash" replaces each identifier with a keyed hash OF that identifier:
        # a code "derived from information about the individual". HHS allows
        # that only under Expert Determination (de-identification guidance
        # Q2.9), so it is refused unless the practice has recorded one.
        return ScanResult(Action.BLOCKED, clean_text="", original_text=text, hits=[],
                          error="redaction_mode: hash codes are derived from identifiers and "
                                "need an Expert Determination on file", profile=config)

    if not text or not text.strip():
        return ScanResult(Action.ALLOW, text, text, [], profile=config)

    # INTAKE (basalt), before anything else.
    # Sanitization must precede detection: a zero-width space inside an
    # SSN defeats the regex entirely, so scanning the raw text would
    # miss it no matter how good the later layers are.
    diag = basalt.diagnose(text)
    scan_text = diag.sanitized_text

    if diag.blocked:
        return ScanResult(
            Action.BLOCKED, clean_text="", original_text=text, hits=[],
            error="; ".join(
                f.code for f in diag.findings if f.severity is basalt.Severity.BLOCK
            ),
            diagnosis=diag,
        )

    try:
        # PATIENT FILE (inkguard/pumice.py): names confirmed anywhere in the
        # patient's file, and the workplaces and facilities it names, are
        # looked for in this note too.
        if file_context is not None:
            memory = set(memory or ()) | set(file_context.names)
        hits, trace = scan_with_trace(
            scan_text, memory=memory,
            typed_memory=(file_context.places if file_context is not None else None),
            file_clinical=(file_context.understood if file_context is not None else frozenset()))
    except Exception as exc:  # noqa: BLE001 — ANY scanner failure stops
        # the note, not just this step.
        return ScanResult(Action.BLOCKED, clean_text="", original_text=text,
                          hits=[], error=f"scanner_failure: {type(exc).__name__}")

    # WHICH identifiers are removed is decided through the shared
    # vocabulary, not a hand-kept list: a recognizer's new subtype
    # inherits its parent's policy, and an unregistered type is PHI.
    configured = set(settings.phi_entities)

    def _removed(entity: str) -> bool:
        return talc.removed_under(entity, is_lds, configured,
                                    keep=set(settings.keep_entities))

    relevant_hits = [h for h in hits if _removed(h.entity_type)]

    # EXIT CHECK, part 1 — mask the uncertain.
    # Evidence scoring (garnet) records how unsure it was about every
    # candidate it held.
    # Person candidates held NARROWLY — within reach of release, and not
    # bound to clinical context — leave as an ambiguous [NAME] rather than
    # as themselves. A detection miss then leaks a placeholder, not a
    # person.
    uncertain: list[PHIHit] = []
    floor = _exit_mask_floor()
    if floor is not None and trace.held:
        from inkguard.garnet import clinically_bound, exit_maskable
        taken = [(h.start, h.end) for h in relevant_hits]
        from inkguard.garnet import unknown_word
        from inkguard.quartz import NAME_BEARING
        # Nursing, scribe and student notes name more people in more
        # places (who was at the bedside, who was paged): the safety net
        # runs in every section for them. See jasper.read_signoff.
        from inkguard.jasper import STRICT_EVERYWHERE, STRICT_KINDS
        _who = getattr(trace, "authorship", {}) or {}
        strict_everywhere = (_who.get("author_class") in STRICT_EVERYWHERE
                             or _who.get("note_kind") in STRICT_KINDS)
        person_spans = [(h.start, h.end) for h in relevant_hits
                         if talc.canonical(h.entity_type) == "PERSON"]
        for r in trace.held:
            if r["entity"] != "PERSON" or not exit_maskable(r, scan_text):
                continue
            # NAME CHAIN. Half of this name was already removed; the other
            # half was held in doubt: "Barrett Wrasman" could leave
            # "Barrett" (an eponym) and "attending Jianhai Mayberry" could
            # leave "Mayberry" (a plan section). A capitalised word fused
            # to a removed name is the rest of that name.
            chained = _chain_remainder(scan_text, r["start"], r["end"], person_spans)
            if chained:
                for cs, ce in chained:
                    if clinically_bound(scan_text, cs, ce) or any(
                            cs < te and ts < ce for ts, te in taken):
                        continue
                    uncertain.append(PHIHit("PERSON", cs, ce, scan_text[cs:ce], r["score"],
                                            "uncertain", r["section"], ("name_chain",)))
                    taken.append((cs, ce))
                continue
            # A word no vocabulary knows — not English, not in the ~85,000
            # clinical terms, not a known device or eponym — is masked
            # wherever it sits and however low it scored: it is either a
            # brand we have not catalogued or a surname we have not seen,
            # and only one of those two mistakes leaks a person. A surname
            # on no name list ("Assessment: Vilallonga with HFrEF") can
            # score as low as -0.10.
            if unknown_word(scan_text[r["start"]:r["end"]]) and r["score"] >= -0.40:
                pass
            elif r["score"] < floor or (r.get("section") not in NAME_BEARING
                                        and not strict_everywhere):
                continue
            # Otherwise the [NAME] safety net runs only in the sections
            # where names live (identity, narrative, signature, unknown). In a procedure or medication section a held KNOWN
            # word is an instrument ("Kocher", "Metzenbaums") far more
            # often than a person.
            s_, e_ = r["start"], r["end"]
            if any(s_ < te and ts < e_ for ts, te in taken):
                continue
            if clinically_bound(scan_text, s_, e_):
                continue
            uncertain.append(PHIHit("PERSON", s_, e_, scan_text[s_:e_],
                                    r["score"], "uncertain", r["section"]))
            taken.append((s_, e_))
    redactions = relevant_hits + uncertain

    # LIMITED DATA SET CONFLICTS. The LDS keeps dates, towns and ZIP codes
    # and removes everything else, so for the first time a word's TYPE
    # decides whether it leaves. Two readings of one span — "Jhosua" a
    # person to one layer and a place to the NER, "Jackson" the patient's
    # surname and the town she lives in — would otherwise fail closed at
    # the ledger or the final inspection and block the note outright. The
    # removed reading wins: the person is removed, and a kept town spelled
    # like a confirmed name is removed too. Removing more is always
    # permitted.
    retained = [h for h in hits if not _removed(h.entity_type)]
    kept_hits: list = []
    if retained:
        conflicts = _kept_type_conflicts(
            scan_text, retained, redactions, trace,
            set(trace.confirmed_names) | set(memory or ()), _removed)
        if conflicts:
            relevant_hits = relevant_hits + conflicts
            redactions = relevant_hits + uncertain
        # Only identifiers the profile KEEPS (dates, towns, ZIP under the
        # LDS), not words that were never identifiers ("CLINIC" as an
        # organisation).
        kept_hits = [h for h in retained if talc.spec(h.entity_type).phi
                     and not any(r.start < h.end and h.start < r.end for r in redactions)]

    # INITIALS BESIDE A REMOVED NAME. "A. Brightwater", "Penfield, Ronald A.",
    # "HUNT, B.": the initial is part of the name and goes with it (an
    # initial is a name's identifying part too).
    redactions = _absorb_initials(scan_text, redactions, extend=(mode != "pseudonym"))
    relevant_hits = [h for h in redactions if h.layer != "uncertain"]

    # LEDGER SETTLEMENT — every detection must have a fate. A detection
    # that was neither redacted nor explained is LOST data: a
    # stage dropped it without saying why. Fail closed.
    lost = []
    if trace.ledger is not None:
        lost = trace.ledger.settle(redactions, not_phi=lambda e: not _removed(e),
                                   text=scan_text)
    if lost and settings.fail_closed:
        return ScanResult(
            Action.BLOCKED, clean_text="", original_text=text, hits=[],
            error=f"ledger: {len(lost)} detection(s) lost between stages",
            diagnosis=diag, trace=trace, ledger=trace.ledger.summary())

    # PSEUDONYMS — codes that carry forward.
    assignments = None
    if mode == "pseudonym":
        try:
            key = pseudo.pseudonym_key()
            table = pseudo.code_table(key)
            pmap = pseudo.PseudonymMap(key=key,
                                       scope=pseudo.scope_for(patient_key, scan_text, key),
                                       known_full_names=set(memory or ()), table=table)
            assignments = pmap.resolve(scan_text, relevant_hits)
        except (date_shift.EphemeralKeyError, pseudo.CodeTableError) as exc:
            # Fail closed: no persistent key or no sound code table means no
            # pseudonyms at all — never codes computed from names instead.
            return ScanResult(Action.BLOCKED, clean_text="", original_text=text,
                              hits=[], error=f"pseudonym: {exc}", diagnosis=diag)

    shifting = bool(date_shift.enabled() and patient_key)
    clean = _apply_redactions(scan_text, redactions, assignments, keep_dates=shifting, mode=mode) \
        if redactions else scan_text

    sh_changes = 0
    if config.apply_safe_harbor_generalization:
        clean, sh_changes = _apply_safe_harbor_rules(clean)

    # DATE SHIFTING (Advanced, off by default). When the caller asked for
    # it, a failure is a BLOCK, so a note never leaves with its original
    # dates because the shift failed (for example, an ephemeral key).
    #
    # Detected dates are left in place for the shifter, which runs before
    # anything masks them; any detected date it could not shift is masked
    # afterwards.
    shift_report = None
    if shifting:
        try:
            clean, shift_report = _shift_dates(clean, redactions, patient_key)
        except Exception as exc:  # noqa: BLE001
            return ScanResult(Action.BLOCKED, clean_text="", original_text=text,
                              hits=[], error=f"date_shift: {exc}", diagnosis=diag)

    shifted_any = bool(shift_report and shift_report.shifted)
    changed = bool(redactions) or sh_changes or shifted_any

    integrity = verify(scan_text, clean) if changed else None

    if changed:
        diag = basalt.diagnose(text, identifier_count=len(relevant_hits))

    contamination = None
    try:
        contamination = contam.analyze(text, relevant_hits)
        if contamination.findings:
            diag.findings.extend(contamination.findings)
    except Exception:
        pass

    linkage_report = None
    try:
        if linkage.enabled():
            linkage_report = linkage.assess(clean)
            if linkage_report.findings:
                diag.findings.extend(linkage_report.findings)
    except Exception:
        pass

    # EXIT CHECK, part 2 — final inspection (obsidian): invariants on the way out.
    confirmed = set(trace.confirmed_names) | set(memory or ())
    exit_report = obsidian.inspect(
        clean, confirmed,
        check_dates=config.apply_safe_harbor_generalization and not shifted_any,
        uncertain_masked=len(uncertain), integrity=integrity,
        linkage=linkage_report)

    ledger_summary = trace.ledger.summary() if trace.ledger is not None else None

    oplog = None
    if want_oplog:
        from inkguard import beryl as _oplog
        oplog = _oplog.build(
            original=text, sanitized=scan_text, redactions=redactions,
            assignments=assignments, trace=trace, config=config,
            clean_after_redaction=_apply_redactions(scan_text, redactions, assignments,
                                                    keep_dates=shifting, mode=mode)
            if redactions else scan_text,
            final=clean, sh_changes=sh_changes, shift_report=shift_report,
            patient_key=patient_key, mode=mode, exit_report=exit_report)

    if exit_report.quarantined and settings.fail_closed:
        return ScanResult(
            Action.BLOCKED, clean_text="", original_text=text, hits=[],
            error="exit_inspection: " + "; ".join(exit_report.reasons),
            diagnosis=diag, exit_report=exit_report, trace=trace,
            ledger=ledger_summary, oplog=oplog, profile=config)

    action = Action.REDACTED if changed else Action.ALLOW

    # FINAL WORD CHECK (agate): a note has no flags only if every word
    # left in it is one the tool can name. Otherwise it is flagged for a
    # person, with the words listed by offset. It fails CLOSED: if the
    # check itself fails, the note goes to review.
    comp = None
    if True:  # the final word check always runs
        from inkguard import agate
        try:
            gen = [(m.start(), m.end()) for m in hipaa.zip_matches(scan_text)] + \
                  [(a.start, a.end) for a in hipaa.find_ages(scan_text) if a.is_identifier]
            comp = agate.assess(
                scan_text, redactions, generalized_spans=gen, lds=is_lds,
                file_words=(file_context.understood if file_context is not None else frozenset()))
        except Exception as exc:  # noqa: BLE001
            comp = agate.failed(f"{type(exc).__name__}")
        if not comp.cleared:
            action = Action.REVIEW
        if oplog is not None:
            oplog["agate"] = {**comp.summary(),
                              "not_understood": [n.to_dict() for n in comp.not_understood]}
    # A NEWSWORTHY EVENT is flagged, not held: the check is an aid to the
    # reviewer, not a decision. The details that pin it down are suggested
    # for removal and the clinician is reminded; see linkage.event_details.
    return ScanResult(action, clean, text, relevant_hits + uncertain,
                      integrity=integrity, diagnosis=diag,
                      contamination=contamination, linkage=linkage_report,
                      profile=config, date_shift_report=shift_report,
                      exit_report=exit_report, trace=trace,
                      ledger=ledger_summary, oplog=oplog,
                      pseudonyms=assignments, comprehension=comp, kept=kept_hits)


def _exit_mask_floor() -> float | None:
    """Evidence score (garnet) at or above which a held PERSON candidate
    is masked as [NAME] on exit. Fixed."""
    return 0.30

