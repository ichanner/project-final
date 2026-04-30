"""Per-entity policy evaluation.

Rules look like:
    name="HYPE under $30", entity_match="Hyperliquid", field="price_usd",
    operator="<", threshold="30"

Operators on the parsed numeric value of the field:
    "<", ">", "<=", ">=", "==", "!="

Operators on the string form:
    "contains", "!contains"

This runs on EVERY poll (fast-path or LLM bootstrap) — the cost discipline
of the rest of the system means policy evaluation is free. We don't query
the rules every poll because that wastes Postgres round-trips; the runner
loads them once per run and passes them to evaluate().
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from .metrics import entity_alerts_fired_total

log = logging.getLogger("scraper.policy")

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)*")


def _parse_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    m = _NUM_RE.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _entity_matches(rule_match: str | None, entity_identity: str) -> bool:
    if not rule_match or rule_match == "*":
        return True
    return rule_match.lower() in entity_identity.lower()


def _evaluate_one(rule: dict, entity: dict, identity: str) -> tuple[bool, str | None]:
    """Returns (fired, observed_value_str)."""
    if not _entity_matches(rule.get("entity_match"), identity):
        return False, None

    field = rule["field"]
    raw = entity.get(field)
    if raw is None:
        return False, None

    op = rule["operator"]
    threshold_str = str(rule["threshold"])

    # String operators
    if op == "contains":
        return (threshold_str.lower() in str(raw).lower()), str(raw)
    if op == "!contains":
        return (threshold_str.lower() not in str(raw).lower()), str(raw)

    # Numeric operators
    val = _parse_number(raw)
    if val is None:
        return False, str(raw)
    threshold = _parse_number(threshold_str)
    if threshold is None:
        return False, str(raw)

    fired = False
    if   op == "<":  fired = val < threshold
    elif op == ">":  fired = val > threshold
    elif op == "<=": fired = val <= threshold
    elif op == ">=": fired = val >= threshold
    elif op == "==": fired = val == threshold
    elif op == "!=": fired = val != threshold
    return fired, str(raw)


def load_rules(cur, source_id: int) -> list[dict]:
    cur.execute(
        "SELECT id, name, entity_match, field, operator, threshold "
        "FROM entity_alert_rules WHERE source_id = %s AND enabled = TRUE",
        (source_id,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def evaluate(
    cur,
    source_id: int,
    sid_label: str,
    run_id: int | None,
    entities_with_ids: Iterable[tuple[int, str, dict]],
    rules: list[dict],
) -> int:
    """Evaluate every (rule × entity) combination on this run's entities.

    `entities_with_ids` is an iterable of (entity_db_id, identity_string, data_dict).
    Returns the number of fires recorded.
    """
    if not rules:
        return 0

    fires = 0
    for entity_db_id, identity, data in entities_with_ids:
        for rule in rules:
            triggered, observed = _evaluate_one(rule, data, identity)
            if not triggered:
                continue
            cur.execute(
                "INSERT INTO entity_alerts "
                "(rule_id, source_id, run_id, entity_id, entity_identity, "
                " field, field_value, threshold, operator) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    rule["id"], source_id, run_id, entity_db_id, identity,
                    rule["field"], observed, str(rule["threshold"]), rule["operator"],
                ),
            )
            entity_alerts_fired_total.labels(
                source_id=sid_label,
                rule_id=str(rule["id"]),
                field=rule["field"],
            ).inc()
            fires += 1
            log.info(
                "policy fire src=%s rule=%s entity=%s field=%s op=%s threshold=%s observed=%s",
                source_id, rule["name"], identity, rule["field"],
                rule["operator"], rule["threshold"], observed,
            )
    return fires
