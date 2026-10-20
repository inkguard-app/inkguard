# Reproducibility and citation

## At a glance

| Item | Version 0.25.0 |
|---|---|
| Determinism | Same version, input and options give the same redacted text, except by design: random names (`--scramble-names`) differ on every run, pseudonym codes and date offsets depend on keys kept on that computer, and every run has its own run id and times |
| Code identity | SHA-256 fingerprint of the engine in every report |
| Benchmarks and scorers | In the repository, with commands to rerun |
| Misses | Published by identifier type |
| Software and human results | Reported separately |
| Test-set policy | From the next release on, each release is measured once on notes written after the previous one; 0.25.0 is measured on the held-out halves |
| Citation metadata | CITATION.cff; a DOI per release on Zenodo |
| Methods text | Draft paragraph in every report |
| Licence | Apache 2.0 (code); CC BY 4.0 (benchmarks) |

## The standard

- **Software citation principles** (FORCE11; Smith, Katz and Niemeyer,
  *PeerJ Computer Science*, 2016): software should be cited by name,
  version and a persistent identifier, and credited like other research
  outputs.
- **Reproducible evaluation**: a reported figure names its test set, its
  denominator, the version measured, and how to rerun it; test data not
  used for development are kept separate.

## Inkguard's position

- Every audit trail records the version and the SHA-256 fingerprint of the
  engine code. Two runs with the same fingerprint used the same code.
- Benchmark figures describe the software alone. Changes made in human
  review are not measured by the software and are never folded into
  published figures.
- Each new version is measured once on invented notes written after the
  previous release; those notes are then published. Earlier results remain
  tied to their own version and are not rerun.

Rerun the published figures:

```bash
git clone https://github.com/inkguard-app/inkguard
cd inkguard
python -m pip install -r requirements.txt
python -m pip install pytest==9.1.1 pytest-timeout==2.4.0
python -m inkguard.cli --self-test
python -m pytest -q --ignore=tests/test_desktop_ui.py
python tools/eval_corpus.py --half held-out
python tools/eval_preservation.py --half held-out
```

## What it needs from you

- Cite the version you used (GitHub: "Cite this repository", from
  [CITATION.cff](../../CITATION.cff)), and its DOI.
- Report the human review as well as the software: who reviewed and how
  decisions were made. The audit trail's optional fields record who ran
  Inkguard and for whom; the review itself is recorded by your team.
- Complete the draft Methods paragraph; it describes the software, not your
  study.

## What it does not do

- It does not make a study's results reproducible; it makes the
  redaction step traceable.
- Each release has a permanent DOI on Zenodo (0.25.0: 10.5281/zenodo.23464777).
