#!/usr/bin/env bash
# Pipeline diario: export → sync → enrich → taste → commit/push de backlog.csv.
# Lo lanza launchd (ops/launchd/com.steambacklog.daily.plist); también vale a mano.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

echo "== $(date '+%F %T') sync diario"
$PY src/export.py </dev/null || echo "⚠ export falló; sigo con los datos anteriores"
$PY src/sync_backlog.py
$PY src/enrich.py
$PY src/taste.py
$PY src/git_sync.py "backlog: sync diario"
echo "== $(date '+%F %T') fin"
