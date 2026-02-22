#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED="$(python3 - <<'PY'
import secrets
print(secrets.randbits(63))
PY
)"

echo "Reroll seed: ${SEED}"
python3 "${ROOT_DIR}/wow_randomizer.py" --seed "${SEED}" --apply "$@"
