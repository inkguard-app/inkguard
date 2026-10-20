<!-- Pull requests are not being accepted yet; see CONTRIBUTING.md. This
     checklist is for changes to the project itself. -->

## What changed and why

## Checklist

- [ ] No patient information anywhere: code, tests, examples, commit messages, screenshots.
- [ ] A test that fails without this change.
- [ ] `./tools/run_tests.sh quick` passes.
- [ ] If detection changed: scored on the benchmark's **tuning** half (never the held-out half), and the result is below.
- [ ] CHANGELOG.md updated.
