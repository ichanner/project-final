#!/usr/bin/env bash
set -euo pipefail

SCRAPER_URL="${SCRAPER_URL:-http://localhost:8080}"
FIXTURE_BASE="${FIXTURE_BASE:-http://fixture}"
PRIMARY="${PRIMARY_MODEL:-google/gemini-2.0-flash-001}"

say() { printf '\n=== %s ===\n' "$1"; }

# OPENROUTER_API_KEY may be empty in PR/fork CI runs (the secret isn't
# exposed to forks). Detect it inside the running extracto container so
# we don't block the integration job on a missing key — we still verify
# the stack comes up healthy and the API surface responds, but skip the
# LLM-dependent assertions.
HAS_LLM_KEY=1
if ! curl -fsS --max-time 5 "$SCRAPER_URL/health" >/dev/null 2>&1; then
  echo "scraper /health unreachable — aborting"
  exit 1
fi
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${EXTRACTO_HAS_KEY:-}" ]; then
  HAS_LLM_KEY=0
fi

run_fixture() {
  local label="$1" path="$2" identity_key="$3" min_count="$4" min_new="$5"
  local fixture_url="$FIXTURE_BASE/$path"

  say "Fixture: $label ($fixture_url) — anchoring_model=$PRIMARY"
  local create_resp
  create_resp=$(curl -fsS -X POST "$SCRAPER_URL/sources" \
    -H 'Content-Type: application/json' \
    -d "{
      \"url\": \"$fixture_url\",
      \"label\": \"$label\",
      \"identity_key\": $identity_key,
      \"schema\": {},
      \"primary_model\": \"$PRIMARY\"
    }")
  echo "$create_resp"
  local source_id
  source_id=$(echo "$create_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')
  echo "source_id=$source_id"

  echo "-- first run --"
  local run_resp
  run_resp=$(curl -fsS --max-time 180 -X POST "$SCRAPER_URL/sources/$source_id/run")
  echo "$run_resp"

  python3 - <<PYCHECK
import json, sys
data = json.loads('''$run_resp''')
assert "primary" in data, f"missing primary in {data}"
assert data["primary"]["entity_count"] >= $min_count, f"expected >=$min_count entities from primary, got {data['primary']['entity_count']}"
assert data["primary"]["new"] >= $min_new, f"expected >=$min_new new on first run, got {data['primary']['new']}"
print(f"ok: primary={data['primary']['entity_count']} entities via {data['primary'].get('source')}")
PYCHECK

  echo "-- second run (idempotence) --"
  local rerun_resp new_after_rerun
  rerun_resp=$(curl -fsS --max-time 180 -X POST "$SCRAPER_URL/sources/$source_id/run")
  echo "$rerun_resp"
  new_after_rerun=$(echo "$rerun_resp" | python3 -c 'import sys, json; print(json.load(sys.stdin)["primary"]["new"])')
  [ "$new_after_rerun" = "0" ] || { echo "FAIL: expected primary new=0 on rerun, got $new_after_rerun" >&2; exit 1; }
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

say "Checking API surface"
curl -fsS "$SCRAPER_URL/sources" >/dev/null \
  || { echo "FAIL: GET /sources failed" >&2; exit 1; }

say "Checking metrics endpoint exposes expected series"
metrics=$(curl -fsS "$SCRAPER_URL/metrics")
echo "$metrics" | grep -q webharvest_scraper_fetch_total \
  || { echo "FAIL: scraper missing webharvest_scraper_fetch_total" >&2; exit 1; }
echo "$metrics" | grep -q webharvest_anchor_re_anchor_total \
  || { echo "FAIL: scraper missing webharvest_anchor_re_anchor_total" >&2; exit 1; }
echo "$metrics" | grep -q webharvest_poll_total \
  || { echo "FAIL: scraper missing webharvest_poll_total" >&2; exit 1; }

if [ "$HAS_LLM_KEY" = "1" ]; then
  run_fixture "jsonld fixture" "jsonld.html" '["name"]' 3 3
  run_fixture "table fixture" "table.html" '["Company"]' 5 5
  echo
  echo "INTEGRATION TEST PASSED (smoke + LLM anchoring on 2 fixtures)"
else
  echo
  echo "INTEGRATION TEST PASSED (smoke only — OPENROUTER_API_KEY not"
  echo "set, skipped LLM-anchoring fixture cases)"
fi
