#!/usr/bin/env bash
# End-to-end integration test. Assumes the stack (with the test overlay) is
# already running and the scraper API is reachable at $SCRAPER_URL.
#
# Strategy: we fan out a single source through the multi-model bake-off using
# two cheap OpenRouter models (gemini-2.0-flash-001 as primary, llama-3.3-70b
# as challenger). Cost per run on a fixture is sub-cent. Requires
# OPENROUTER_API_KEY to be set in the stack's environment.
set -euo pipefail

SCRAPER_URL="${SCRAPER_URL:-http://localhost:8080}"
FIXTURE_BASE="${FIXTURE_BASE:-http://fixture}"
PRIMARY="${PRIMARY_MODEL:-google/gemini-2.0-flash-001}"
CHALLENGER="${CHALLENGER_MODEL:-meta-llama/llama-3.3-70b-instruct}"

say() { printf '\n=== %s ===\n' "$1"; }

run_fixture() {
  local label="$1" path="$2" identity_key="$3" min_count="$4" min_new="$5"
  local fixture_url="$FIXTURE_BASE/$path"

  say "Fixture: $label ($fixture_url) — primary=$PRIMARY challenger=$CHALLENGER"
  local create_resp
  create_resp=$(curl -fsS -X POST "$SCRAPER_URL/sources" \
    -H 'Content-Type: application/json' \
    -d "{
      \"url\": \"$fixture_url\",
      \"label\": \"$label\",
      \"identity_key\": $identity_key,
      \"schema\": {},
      \"primary_model\": \"$PRIMARY\",
      \"comparison_models\": [\"$CHALLENGER\"]
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
assert len(data.get("challengers", [])) >= 1, "expected at least one challenger run"
ch = data["challengers"][0]
assert ch.get("agreement") is not None, "challenger missing agreement score"
print(f"ok: primary={data['primary']['entity_count']} entities, challenger={ch['entity_count']}, agreement={ch['agreement']:.2f}")
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

run_fixture "jsonld fixture" "jsonld.html" '["name"]' 3 3
run_fixture "table fixture" "table.html" '["Company"]' 5 5

say "Checking metrics endpoints expose expected series"
curl -fsS "$SCRAPER_URL/metrics" | grep -q webharvest_scraper_fetch_total \
  || { echo "FAIL: scraper missing webharvest_scraper_fetch_total" >&2; exit 1; }
curl -fsS "$SCRAPER_URL/metrics" | grep -q webharvest_agreement_jaccard \
  || { echo "FAIL: scraper missing webharvest_agreement_jaccard" >&2; exit 1; }

echo
echo "INTEGRATION TEST PASSED (jsonld + table fixtures, multi-model bake-off)"
