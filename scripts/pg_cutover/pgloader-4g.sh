#!/usr/bin/env bash
# pgloader with a 4 GB Lisp heap.
#
# Default 1 GB heap is flaky on this catalog (Heap exhausted → Lisp debugger
# holding a COPY session; needs kill -9). Attempt 3 should use this wrapper:
#   PGLOADER=scripts/pg_cutover/pgloader-4g.sh
#   timeout -s KILL 600 ./scripts/pg_cutover.sh --apply-load … </dev/null
set -euo pipefail
exec pgloader --dynamic-space-size 4096 "$@"
