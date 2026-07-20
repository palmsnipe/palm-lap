#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

python3 -m compileall -q "$REPO_ROOT/src"
for script in "$REPO_ROOT"/scripts/*.sh; do
    sh -n "$script"
done
for config in "$REPO_ROOT"/config/*.json.example; do
    python3 -m json.tool "$config" >/dev/null
done

if grep -R -n -E \
    '00:07:E0|00:70:E0|E4:5F:01|/Users/cyril|/home/cyril|192\.168\.1\.29|Koti AP|gho_[A-Za-z0-9]|LinkKey' \
    --exclude=check.sh --exclude-dir=.git --exclude-dir=__pycache__ "$REPO_ROOT"; then
    echo "Host-specific identifier or secret-like value found." >&2
    exit 1
fi

if find "$REPO_ROOT" -type f \( -name '*.prc' -o -name '*.pdb' -o -name '*.zip' \) -print | grep .; then
    echo "Palm packages and archives are runtime data and must not be committed." >&2
    exit 1
fi

echo "Repository checks passed."
