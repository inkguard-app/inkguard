# Contributing

Thank you for helping. A few rules keep patient information out of
this project.

## The one rule that matters most

**Never put real patient information anywhere in this project**: not in an
issue, a pull request, a test, a commit message, a screenshot or an email.
Invent your examples. If you found a miss on a real note, describe its
*shape* ("a surname in capitals after DAUGHTER") and write an invented note
that reproduces it.

## Reporting a problem

Report problems by email to **inkguard.project@gmail.com**. After every run,
Inkguard writes `6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx` in the results folder:
already filled in with the version, run id, computer type, counts, support
codes and the reasons files were flagged, and nothing from your files. Write
anything else in its open box, check it with
`inkguard --check-feedback <file>`, and email it to inkguard.project@gmail.com. Describe problems with made-up text.

- **A missed identifier, or clinical text removed by mistake:** describe its
  shape with an invented example, what you expected and what you got.
- **A security problem:** follow [SECURITY.md](SECURITY.md).
- **A support code on the screen:** include the code (for example
  `RUN-DISK-FULL`). [SUPPORT_CODES.md](SUPPORT_CODES.md) explains each one.

Every report is read. We may not reply individually; fixes are listed in the
release notes.

## Pull requests

**We are not accepting pull requests yet.** Bug reports are very welcome. The
steps below describe how the project itself is changed, and will apply to
outside contributions once they open.

## Changing the code

1. Fork the repository and create a branch.
2. Add a test that fails without your change: an identifier that must come
   out, or clinical text that must stay. `tests/test_corpus_shapes.py` shows
   the pattern.
3. Run the tests:
   ```bash
   python -m pip install -r requirements.txt
   python -m pip install pytest==9.1.1 pytest-timeout==2.4.0
   python -m pytest -q --ignore=tests/test_desktop_ui.py
   ```
4. If you changed detection, score the benchmark's **tuning** half and say
   what changed in the pull request:
   ```bash
   python tools/eval_corpus.py --half tuning
   ```
   Please do not tune against the held-out half. It exists so the accuracy
   this project reports stays honest.
5. Open a pull request describing what changed and why.

### Ground rules for the code

- The shell reaches the engine only through `inkguard/contract.py`
  (`tests/test_boundary.py` checks this).
- No network code, ever (`tests/test_no_network.py` checks this).
- No claims the software cannot back up. `tests/test_claims.py` lists the
  phrases that are never used, and checks the app, its screens and the
  documents for them.
- A security control named in `docs/SECURITY_STANDARDS.md` cites the tests
  that prove it, and those tests must exist (`tests/test_docs_cite_real_tests.py`).

## Code of conduct

Be kind and assume good faith. Harassment of any kind is not tolerated; the
maintainers may remove comments or contributors that break this.
