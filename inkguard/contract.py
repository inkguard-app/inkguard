"""
contract — the one door between the core engine and the shell.

The tool is built in two halves (see docs/HOW_IT_WORKS.md):

  - the CORE engine, which decides what is PHI given text, and
  - the SHELL (file formats, the intake guard, the review workflow, reports,
    the interface), which does everything about files, screens and delivery.

They are meant to meet HERE and nowhere else. Every operation the shell needs
from the engine is one function in this module, with a typed result; the
shell is not supposed to import anything from the engine's internals
(`policy`, `phi_scanner`, `garnet`, `quartz`, `hipaa`, `talc`, …). A boundary
test enforces that. Keeping the boundary in one place lets the core be
tested on its own, and later moved to its own process or rewritten, without
the shell noticing.

The rule the whole split follows: TEXT IN, POSITIONS OUT. The core is handed
plain text and answers with character ranges and their replacements — ready
to apply. It never sees a file, a file name, or anything on screen.

CONTRACT_VERSION is bumped when the shape of these results changes, so a
shell and a core that were built apart can check they still fit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTRACT_VERSION = "1.0"

# The one reason a shell module may show or share when the engine stops a
# file. It names no internal stage and carries no detail: the engine's own
# error text can quote fragments of the note, so a report the user shares
# with someone helping them stays short and contains nothing from the note.
# Every shell report sink uses THIS constant for a blocked file; the raw
# engine error lives only on DeidResult.raw / the user's private oplog.
BLOCKED_REASON = "the tool stopped on this file as a precaution"


# ---------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------

@dataclass
class DeidResult:
    """What the core says about one piece of text.

    `edits` is the whole answer a caller usually needs: a list of
    (start, end, replacement) ready to apply to the exact text that was
    passed in. `status` is 'clear' (no words flagged; still needs human review), 'review' (release only
    after a person looks at `words_to_check`), or 'stop' (the engine refused;
    do not release)."""
    status: str = "clear"                      # clear | review | stop
    reason: str = ""
    edits: list = field(default_factory=list)                  # (start, end, replacement)
    clean_text: str = ""
    words_to_check: list = field(default_factory=list)         # {word, reason, holds_the_note}
    removed_person_names: set = field(default_factory=set)
    secrets: set = field(default_factory=set)                  # the exact strings removed
    report_facts: dict = field(default_factory=dict)           # counts/types, never text
    reproduced: bool = True                    # the edits rebuild the engine's own output exactly
    oplog: object = None                       # the user's private operation log, when asked for
    raw: object = None                         # the underlying engine result (transitional)

    @property
    def ok(self) -> bool:
        return self.status != "stop"


# ---------------------------------------------------------------------
# Turning an engine decision into ready-to-apply edits.
# This is core-side work (it depends only on engine modules), kept here so
# the contract is the single owner of "decision -> positions".
# ---------------------------------------------------------------------

def placeholder_for(span: dict, text: str) -> str:
    """What one removal leaves behind. A span exactly as the engine made it
    is rendered the engine's way (a date keeps its year under Safe Harbor).
    A span a reviewer made or touched gets the plain placeholder: a year
    pulled out of a reviewer's selection could be the last digits of a phone
    number."""
    from inkguard import talc, policy
    from inkguard.phi_scanner import PHIHit
    if span.get("layer") == "review":
        out = talc.placeholder(span["entity"]) if talc.is_registered(span["entity"]) \
            else "[REDACTED]"
    else:
        out = policy._replacement(PHIHit(span["entity"], span["start"], span["end"], text, 1.0,
                                         span.get("layer", "review")), None)
    return opaque(out) if opaque_enabled() else out


def merge_spans(spans: list[dict]) -> list[dict]:
    """Non-overlapping removal spans. Overlapping pieces merge into one span
    rendered as a REVIEW span (plain placeholder): a name if any piece was
    one, the shared type if all pieces agree, otherwise a generic identifier.
    A span that stands alone keeps its own layer, so with no reviewer changes
    the engine's output is reproduced exactly."""
    out: list[dict] = []
    for s in sorted(spans, key=lambda x: (x["start"], -x["end"])):
        if out and s["start"] < out[-1]["end"]:
            last = out[-1]
            last["end"] = max(last["end"], s["end"])
            ents = {last["entity"], s["entity"]}
            last["entity"] = ("PERSON" if "PERSON" in ents else
                              last["entity"] if len(ents) == 1 else "OTHER_IDENTIFIER")
            last["layer"] = "review"
            continue
        out.append({"start": s["start"], "end": s["end"], "entity": s["entity"],
                    "layer": s.get("layer", "review")})
    return out


def _edits_from_result(text: str, res, lds: bool) -> tuple[list, list]:
    """The removals the engine made, plus the Safe Harbor generalizations
    (ZIP, age 90+) unless the profile is the Limited Data Set, as a sorted
    list of (start, end, replacement) and the merged person-bearing spans.
    Mirrors what the review payload builds, for the edit-relevant fields."""
    from inkguard import hipaa, talc
    removals = []
    taken: list[tuple[int, int]] = []
    for h in sorted(res.hits or [], key=lambda h: h.start):
        removals.append({"start": h.start, "end": h.end,
                         "entity": talc.canonical(h.entity_type), "layer": h.layer})
        taken.append((h.start, h.end))
    generalized = []
    if not lds:
        from inkguard.hipaa import RESTRICTED_ZIP_PREFIXES
        for m in hipaa.zip_matches(text):
            if not any(m.start() < b and a < m.end() for a, b in taken):
                p = m.group(1)[:3]
                generalized.append((m.start(), m.end(),
                                    "000**" if p in RESTRICTED_ZIP_PREFIXES else f"{p}**"))
        for a in hipaa.find_ages(text):
            if a.is_identifier and not any(a.start < y and x < a.end for x, y in taken):
                generalized.append((a.start, a.end, a.replacement))
    spans = merge_spans(removals)
    edits = [(s["start"], s["end"], placeholder_for(s, text[s["start"]:s["end"]])) for s in spans]
    edits += list(generalized)
    return sorted(edits), spans


def _apply(text: str, edits: list) -> str:
    out, pos = [], 0
    for s, e, rep in sorted(edits):
        out.append(text[pos:s])
        out.append(rep)
        pos = e
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------

def deidentify(text: str, *, profile: str = "safe_harbor", mode: str = "mask",
               patient_key: str | None = None, memory=None, knowledge=None,
               prevent_date_shift: bool = False, want_oplog: bool = False,
               engine=None) -> DeidResult:
    """Redact identifiers in one piece of text. TEXT IN, POSITIONS OUT.

    `profile` is 'safe_harbor' or 'limited_data_set'; `mode` is 'mask'
    (bracket placeholders) or 'pseudonym'; `knowledge` is the opaque token
    from `learn` (one patient's whole-file context), passed straight back.
    `prevent_date_shift` keeps dates in place even when date shifting is
    switched on globally — documents need it, because a shifted date could
    not be placed back on the page exactly. The result carries ready-to-apply
    `edits` and a `status` the shell acts on. The core is never told the
    file, path or screen any of this came from.

    `want_oplog` builds the user's private operation log on the result.
    `engine` lets a caller inject the engine entry point (the same
    `policy.evaluate`); it defaults to the real engine and exists so the shell
    can pass an injected one through without importing the engine itself."""
    from inkguard import date_shift
    if engine is None:
        from inkguard.policy import evaluate as engine
    if prevent_date_shift and date_shift.enabled():
        patient_key = None
    res = engine(text, patient_key=patient_key, profile_name=profile,
                 memory=set(memory or ()), redaction_mode=mode, want_oplog=want_oplog,
                 **({"file_context": knowledge} if knowledge is not None else {}))
    if res.action.value == "blocked":
        # The reason the shell may show or share is deliberately GENERIC: the
        # engine's own error names internal stages and can quote fragments of
        # the note, so it stays out of any shareable report. The raw error
        # stays on `raw` for the user's private operation log only.
        return DeidResult(status="stop", reason=BLOCKED_REASON,
                          raw=res, oplog=getattr(res, "oplog", None),
                          report_facts={"action": "blocked"})
    lds = profile == "limited_data_set"
    edits, spans = _edits_from_result(text, res, lds)
    clean_text = res.clean_text
    if opaque_enabled() and mode == "mask":
        edits = [(s, e, opaque(r)) for s, e, r in edits]
        clean_text = opaque(clean_text)
    # `edits` are placeholder-form (ready to black out a document). `clean_text`
    # is the engine's own authoritative output for the mode asked for — the
    # same as `edits` in mask mode, but the pseudonymised text in pseudonym
    # mode, where the codes cannot be a simple black-out. `reproduced` says
    # whether applying the edits rebuilds that output exactly: true in mask
    # mode (what the document path relies on), false in pseudonym mode, where
    # a caller must use `clean_text`, not re-render the edits.
    rendered = _apply(text, edits)
    words = []
    comp = getattr(res, "comprehension", None)
    if comp is not None:
        blocking = set(map(id, comp.blocking))
        words = [{"word": text[n.start:n.end], "reason": n.reason,
                  "holds_the_note": id(n) in blocking} for n in comp.not_understood]
    facts: dict = {"action": res.action.value, "removed": {}}
    for h in (res.hits or []):
        t = h.entity_type
        facts["removed"][t] = facts["removed"].get(t, 0) + 1
    return DeidResult(
        status="review" if res.action.value == "review" else "clear",
        edits=edits, clean_text=clean_text, words_to_check=words,
        removed_person_names={text[s["start"]:s["end"]] for s in spans if s["entity"] == "PERSON"},
        secrets={text[s:e] for s, e, _ in edits},
        report_facts=facts, reproduced=(rendered == clean_text),
        oplog=getattr(res, "oplog", None), raw=res)


# Opaque placeholders: every removal leaves the same marker, so the page does
# not show what kind of thing was there. Intended for final files (in the
# review workflow, in development, after review and sign-off); drafts keep
# typed markers so the people checking them can see what each removal was.
# Switched on for one export at a time, never globally.
OPAQUE_MARK = "[REMOVED]"
import contextlib as _contextlib  # noqa: E402
import contextvars as _contextvars  # noqa: E402
_OPAQUE = _contextvars.ContextVar("opaque_markers", default=False)


def opaque_enabled() -> bool:
    return _OPAQUE.get()


@_contextlib.contextmanager
def opaque_scope(on: bool = True):
    """Opaque markers for everything rendered inside this block (one export)."""
    token = _OPAQUE.set(bool(on))
    try:
        yield
    finally:
        _OPAQUE.reset(token)


def opaque(text: str) -> str:
    """Every typed placeholder ([NAME], [DATE], [MRN], [FACILITY] ...) becomes
    the one marker. Kept words beside a placeholder ("[REMOVED] Hospital")
    are clinical context and stay."""
    import re
    from inkguard import talc
    marks = set(talc.PLACEHOLDER.values()) | {"[REDACTED]", "[NAME]"}
    rx = re.compile("|".join(re.escape(m) for m in sorted(marks, key=len, reverse=True)))
    return rx.sub(OPAQUE_MARK, text)


def learn(texts, *, patient_key: str | None = None, profile: str | None = None):
    """Pass 1 of the whole-patient read: learn one patient's confirmed names,
    workplaces and clinical word use across all their notes. Returns an opaque
    token the shell holds and passes back to `deidentify` as `knowledge`; the
    shell never looks inside it."""
    from inkguard import pumice
    return pumice.learn(list(texts), patient_key=patient_key, profile_name=profile)


def classify(text: str) -> dict:
    """The shape of a piece of text — structured form vs narrative, and which
    labelled fields it carries — for routing and the intake guard. No
    decision about PHI is made here."""
    from inkguard import input_classifier as ic
    cls = ic.classify(text)
    fields = sorted({f.entity_type for f in ic.extract_anchored_fields(text)}) \
        if getattr(cls.profile, "use_field_anchors", False) else []
    kind = getattr(cls, "input_class", None)
    return {"kind": getattr(kind, "value", str(kind)) if kind is not None else "unknown",
            "labelled_fields": fields}


def sanitize(text: str) -> str:
    """The normalisation the engine will apply to its input (adversarial
    characters, encoding debris), exposed so the shell can normalise each
    small piece of a document before joining them, and feed the engine
    exactly what it will read. No PHI decision is made here."""
    from inkguard import basalt
    return basalt.diagnose(text).sanitized_text if text else text


def canonical_type(entity_type: str) -> str:
    """The engine's canonical name for an identifier type (several detector
    labels collapse to one), for the shell's counts and reports."""
    from inkguard import talc
    return talc.canonical(entity_type)


def seed_names(texts) -> set[str]:
    """The names a patient's own notes confirm by structure alone — labels,
    titles, age frames, no model — the cheap first-pass seeds. Core
    knowledge, exposed so the shell can learn across one patient's notes
    without importing the engine. Lives in memory for one patient only."""
    from inkguard import shale, basalt
    from inkguard.phi_scanner import structural_scan
    names: set[str] = set()
    for t in texts:
        names |= shale.collect_seed_names(basalt.diagnose(t).sanitized_text, structural_scan)
    return names


def is_known_name(token: str) -> bool:
    """Whether a token is on the engine's name lists (a surname or given
    name). For the shell's file- and folder-naming, which must not let a
    name into an output path."""
    from inkguard.name_registry import NAMES
    return token.lower() in NAMES


def profile_info(profile: str) -> dict:
    """A de-identification profile's label and the legal metadata the report
    states: the method's citation, whether the output is still PHI, whether a
    data use agreement is needed, and the obligations that come with it."""
    from inkguard import deident_profile
    c = deident_profile.get_profile(profile)
    return {"profile": c.profile.value, "citation": c.citation, "description": c.description,
            "is_still_phi": c.is_still_phi, "requires_dua": c.requires_dua,
            "obligations": c.obligations}


# ---------------------------------------------------------------------
# Primitives the review workflow (chert, in development) composes its page
# from. Each is a small piece of engine knowledge, so the review shell reaches
# it through the contract instead of importing the engine.
# ---------------------------------------------------------------------

def process_patient(texts, *, patient_key=None, profile="safe_harbor", mode="mask",
                    should_stop=None):
    """The whole-patient read: learn every note's names, then redact each note
    with that knowledge, reading again until nothing new is learned (at least
    three reads). `should_stop` is asked between notes; when it says stop,
    `Stopped` is raised and nothing is returned. Returns (results, knowledge)."""
    from inkguard import pumice
    return pumice.process(list(texts), patient_key=patient_key, profile_name=profile,
                          redaction_mode=mode, should_stop=should_stop)


class Stopped(Exception):
    """The caller asked the whole-patient read to stop part way."""


def anchored_fields(text: str) -> list[tuple[int, int]]:
    """The spans of values that sit under a field label ("MRN:", "Patient:"),
    which are certain whichever layer claimed them."""
    from inkguard import input_classifier as ic
    return [(a.start, a.end) for a in ic.extract_anchored_fields(text)]


def replacement(hit) -> str:
    """What one engine hit is shown as in the blacked-out view (a date keeps
    its year under Safe Harbor)."""
    from inkguard import policy
    return policy._replacement(hit, None)


def zip_generalizations(text: str) -> list[tuple[int, int, str]]:
    """Safe Harbor ZIP generalizations in the text: (start, end, shows)."""
    from inkguard import hipaa
    from inkguard.hipaa import RESTRICTED_ZIP_PREFIXES
    out = []
    for m in hipaa.zip_matches(text):
        p = m.group(1)[:3]
        out.append((m.start(), m.end(), "000**" if p in RESTRICTED_ZIP_PREFIXES else f"{p}**"))
    return out


def age_generalizations(text: str) -> list[tuple[int, int, str]]:
    """Safe Harbor age-90+ generalizations in the text: (start, end, shows)."""
    from inkguard import hipaa
    return [(a.start, a.end, a.replacement) for a in hipaa.find_ages(text) if a.is_identifier]


def newsworthy_events(text: str) -> list[tuple[str, int, int]]:
    """Sentences describing an event that may have been in the news (a
    crash with many hurt, a disaster, a shooting): (kind, start, end). A
    quasi-identifier for review, never removed by the tool on its own."""
    from inkguard import linkage
    return linkage.find_events(text)


def event_details(text: str) -> list[tuple[str, int, int]]:
    """Inside newsworthy-event sentences, the details that pin the event
    down: ('name' | 'count', start, end). Suggested for removal."""
    from inkguard import linkage
    return linkage.event_details(text)


def placeholders() -> dict:
    """The placeholder word shown for each identifier type, for the page."""
    from inkguard import talc
    return talc.PLACEHOLDER


def apply_safe_harbor(text: str) -> str:
    """Apply the Safe Harbor generalizations (ZIP to three digits, age 90+)
    to already-redacted text, for the released copy."""
    from inkguard import policy
    clean, _ = policy._apply_safe_harbor_rules(text)
    return clean


def rare_diseases() -> tuple:
    """The rare-disease lexicon and its longest phrase length."""
    from inkguard.lexicon_rare import RARE_DISEASES, MAX_WORDS
    return RARE_DISEASES, MAX_WORDS


_RAW_OLD_AGE = re.compile(r"\b(?:9\d|1[01]\d)[ -]?(?:y/?o\b|yo\b|yrs?\b|years?\b|-year)", re.I)


def shapes_left(clean: str, lds: bool) -> dict:
    """Identifier SHAPES left after all reviews: structured ones (phone, SSN,
    email, record number, date) and, under Safe Harbor, an age of 90+ or a
    full ZIP code. The final inspection at release."""
    from inkguard import obsidian, hipaa
    left = dict(obsidian.residual_structured(clean, check_dates=not lds))
    if not lds:
        if any(a.is_identifier for a in hipaa.find_ages(clean)) or _RAW_OLD_AGE.search(clean):
            left["AGE_90_PLUS"] = 1
        if any(True for _ in hipaa.zip_matches(clean)):
            left["ZIP"] = 1
    return left


def engine():
    """The engine entry point the shell binds to, so the shell gets its
    binding HERE instead of importing the engine. In-process this is the real
    evaluate; if the core ever runs as its own process, this is the one place
    that changes, with the same calling shape. A caller that only needs a clean
    result should prefer `deidentify`; this exists for the batch tool's
    whole-patient passes, which consume the engine's own rich result."""
    from inkguard.policy import evaluate
    return evaluate


def pseudonym_ready() -> str | None:
    """Check, once and before a run, that pseudonym codes can be given: a
    persistent root key and a sound code table at PHI_CODE_TABLE. Returns
    None when ready, or a short plain reason (no key material, no path) the
    shell turns into a support code. Opening the table here also runs its
    integrity check, so a damaged table is caught before any file is read."""
    from inkguard import date_shift, onyx
    try:
        key = onyx.pseudonym_key()
        onyx.code_table(key).check()        # re-read now, even if already open
    except date_shift.EphemeralKeyError:
        return "no persistent root key is set"
    except onyx.CodeTableError as exc:
        return str(exc)
    return None


def check_code_table(path) -> str | None:
    """Verify a code table file (e.g. inside a backup being tested) without
    touching the live one. Returns None when sound, or a short plain reason."""
    from inkguard import date_shift, onyx
    try:
        onyx.CodeTable(path, onyx.pseudonym_key())
    except date_shift.EphemeralKeyError:
        return "no persistent root key is set"
    except onyx.CodeTableError as exc:
        return str(exc)
    return None


def pseudonym_setup() -> dict:
    """First-time setup: create the code table at PHI_CODE_TABLE. Refuses if
    one is already there. Returns the table's id and size (no key material)."""
    from inkguard import onyx
    return onyx.create_code_table(onyx._env_table_path(), onyx.pseudonym_key()).info()


def pseudonym_accept_restored() -> dict:
    """Knowingly accept a table restored from an older backup: people coded
    after that backup get new codes from now on."""
    from inkguard import onyx
    return onyx.accept_restored_table(onyx.pseudonym_key())


def code_table_restore(path, data: bytes) -> dict:
    """Disaster recovery: put a code table from a checked backup back at
    `path` (an absolute path). The incoming table is verified first; a table
    already there is kept beside it as evidence, never deleted; the restored
    size becomes this computer's new watermark. The caller decides WHEN this
    is allowed (only when the live table is missing or fails its check)."""
    import datetime as _dt
    import os
    import tempfile
    from pathlib import Path
    from inkguard import onyx
    p = Path(path)
    if not p.is_absolute():
        raise onyx.CodeTableError("the code table location must be a full path")
    with tempfile.TemporaryDirectory(prefix="code-table-check-") as tmp:
        probe = Path(tmp) / "code_table.json"
        probe.write_bytes(data)
        reason = check_code_table(probe)
        if reason:
            raise onyx.CodeTableError(reason)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        os.replace(p, p.with_name(f"{p.name}.damaged-{stamp}"))
    fd, tmp = tempfile.mkstemp(prefix=".restore.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return onyx.accept_restored_table(onyx.pseudonym_key(), path=p)


def pseudonym_table_info() -> dict | None:
    """The table's id and size, for the run report; None if not open."""
    from inkguard import onyx
    try:
        return onyx.code_table(onyx.pseudonym_key()).info()
    except Exception:  # noqa: BLE001
        return None


def version() -> dict:
    """What a shell checks at startup to be sure it fits this core."""
    from inkguard import __version__
    return {"contract": CONTRACT_VERSION, "build": __version__}
