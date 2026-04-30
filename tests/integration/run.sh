#!/usr/bin/env bash
# End-to-end integration test. Assumes the stack (with the test overlay) is
# already running and the scraper API is reachable at $SCRAPER_URL.
#
# Strategy: the local heuristic extractor returns ~0.85 confidence on JSON-LD
# pages and ~0.78 on header-bearing tables — both above the default 0.7
# threshold. So this test exercises the full pipeline (fetch -> snapshot ->
# local extract -> diff -> persist) on two distinct extraction paths without
# needing an Anthropic API key.
set -euo pipefail

SCRAPER_URL="${SCRAPER_URL:-http://localhost:8080}"
FIXTURE_BASE="${FIXTURE_BASE:-http://fixture}"

say() { printf '\n=== %s ===\n' "$1"; }

# Run a single fixture: create a source, trigger a run, assert results.
# Args: $1=label  $2=path  $3=identity_key (JSON array)  $4=expected_min_count
#       $5=expected_min_first_run_new
run_fixture() {
  local label="$1" path="$2" identity_key="$3" min_count="$4" min_new="$5"
  local fixture_url="$FIXTURE_BASE/$path"

  say "Fixture: $label ($fixture_url)"
  local create_resp
  create_resp=$(curl -fsS -X POST "$SCRAPER_URL/sources" \
    -H 'Content-Type: application/json' \
    -d "{
      \"url\": \"$fixture_url\",
      \"label\": \"$label\",
      \"identity_key\": $identity_key,
      \"schema\": {}
    }")
  echo "$create_resp"
  local source_id
  source_id=$(echo "$create_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')
  echo "source_id=$source_id"

  echo "-- first run --"
  local run_resp
  run_resp=$(curl -fsS -X POST "$SCRAPER_URL/sources/$source_id/run")
  echo "$run_resp"

  local backend entity_count new_count
  backend=$(echo "$run_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("backend", "?"))')
  entity_count=$(echo "$run_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("entity_count", 0))')
  new_count=$(echo "$run_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("new", 0))')

  [ "$backend" = "local" ] || { echo "FAIL: expected local backend, got $backend (escalation triggered — heuristic confidence below threshold)" >&2; exit 1; }
  [ "$entity_count" -ge "$min_count" ] || { echo "FAIL: expected >=$min_count entities, got $entity_count" >&2; exit 1; }
  [ "$new_count" -ge "$min_new" ] || { echo "FAIL: expected >=$min_new new entities on first run, got $new_count" >&2; exit 1; }

  echo "-- second run (idempotence) --"
  local rerun_resp new_after_rerun
  rerun_resp=$(curl -fsS -X POST "$SCRAPER_URL/sources/$source_id/run")
  echo "$rerun_resp"
  new_after_rerun=$(echo "$rerun_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("new", -1))')
  [ "$new_after_rerun" = "0" ] || { echo "FAIL: expected new=0 on rerun (idempotent), got $new_after_rerun" >&2; exit 1; }

  echo "-- entities persisted --"
  curl -fsS "$SCRAPER_URL/sources/$source_id/entities" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert len(data) >= $min_count, f'expected >= $min_count entities, got {len(data)}'
print(f'ok: {len(data)} entities for $label')
"
}

say "Waiting for scraper /health"
for i in $(seq 1 30); do
  if curl -fsS "$SCRAPER_URL/health" >/dev/null 2>&1; then
    echo "scraper ready"
    break
  fi
  sleep 2
  if [ "$i" = "30" ]; then echo "scraper never came up" >&2; exit 1; fi
done

# Fixture 1: JSON-LD path. Local model expects high confidence (~0.85).
run_fixture "jsonld fixture" "jsonld.html" '["name"]' 3 3

# Fixture 2: HTML table path. Local model expects medium-high confidence (~0.78).
run_fixture "table fixture" "table.html" '["Company","Filing Date"]' 5 5

say "Checking metrics endpoints expose expected series"
curl -fsS "$SCRAPER_URL/metrics" | grep -q webharvest_scraper_fetch_total \
  || { echo "FAIL: scraper missing webharvest_scraper_fetch_total" >&2; exit 1; }
curl -fsS "$SCRAPER_URL/metrics" | grep -q webharvest_scraper_run_entities_total \
  || { echo "FAIL: scraper missing webharvest_scraper_run_entities_total" >&2; exit 1; }

echo
echo "INTEGRATION TEST PASSED (jsonld + table fixtures)"
