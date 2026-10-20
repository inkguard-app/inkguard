#!/bin/sh
# Install the pre-upload check as a git pre-commit hook: every commit is
# checked, and a commit carrying a key, a run artifact, patient data or
# private-project material is refused before it exists.
set -e
cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || { echo "Run 'git init' first."; exit 1; }
cat > .git/hooks/pre-commit <<'HOOK'
#!/bin/sh
python tools/preflight_check.py --staged || {
  echo "Commit refused by the pre-upload check."; exit 1; }
HOOK
chmod +x .git/hooks/pre-commit
echo "Pre-upload check installed as a pre-commit hook."
