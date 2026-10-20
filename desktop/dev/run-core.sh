#!/bin/sh
# Development only (never bundled): runs the secure core from the source tree.
# Point a debug build of the desktop app at it with
#   DEID_CORE_DEV=/path/to/desktop/dev/run-core.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT" && PYTHONPATH="$ROOT" exec python3 desktop/opal_service.py
