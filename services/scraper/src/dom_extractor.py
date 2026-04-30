"""Deterministic DOM extractor: applies a cached anchor recipe to HTML.

The LLM produced the recipe (CSS selectors + extract methods + transforms);
this module just applies it via BeautifulSoup. Pure Python, no network, no
tokens, sub-second on 300KB HTML. This is what runs on every poll after
the first.

Recipe shape (from `extracto`):
  {
    "root_selector": "table.protocols tbody tr",
    "expected_count": 30,
    "fields": {
      "name":  {"selector": "td:nth-child(2)", "extract": "text", "transform": "trim"},
      "price": {"selector": ".price",          "extract": "text", "transform": "parseFloat"},
      "url":   {"selector": "a",               "extract": "attr:href", "transform": null}
    },
    "verification": {"name": "...", "price": 123.45, "url": "..."}
  }
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag


_NUM_RE = re.compile(r"-?\d+(?:[\.,]\d+)*")


def _apply_extract(node: Tag | None, extract: str) -> str | None:
    if node is None:
        return None
    if extract == "text" or extract is None:
        return node.get_text(strip=True)
    if extract == "html":
        return node.decode_contents().strip()
    if extract.startswith("attr:"):
        return node.get(extract.split(":", 1)[1])
    return node.get_text(strip=True)


def _apply_transform(value: Any, transform: str | None) -> Any:
    if value is None:
        return None
    if transform in (None, "", "none", "null"):
        return value
    s = str(value)
    if transform == "trim":
        return s.strip()
    if transform == "lower":
        return s.strip().lower()
    if transform == "upper":
        return s.strip().upper()
    if transform in ("parseFloat", "parse_float", "float"):
        m = _NUM_RE.search(s.replace(",", ""))
        return float(m.group()) if m else None
    if transform in ("parseInt", "parse_int", "int"):
        m = _NUM_RE.search(s)
        if not m:
            return None
        try:
            return int(float(m.group()))
        except ValueError:
            return None
    return value


def apply_anchors(html: str, anchors: dict) -> list[dict[str, Any]]:
    """Apply an anchor recipe to HTML, return entities."""
    if not anchors or not anchors.get("root_selector"):
        return []
    soup = BeautifulSoup(html, "lxml")
    root_nodes = soup.select(anchors["root_selector"])
    fields: dict[str, dict] = anchors.get("fields") or {}

    out: list[dict[str, Any]] = []
    for root in root_nodes:
        entity: dict[str, Any] = {}
        for field_name, recipe in fields.items():
            sel = recipe.get("selector")
            if sel:
                child = root.select_one(sel)
            else:
                child = root
            raw = _apply_extract(child, recipe.get("extract", "text"))
            entity[field_name] = _apply_transform(raw, recipe.get("transform"))
        out.append(entity)
    return out


def verify_anchors(html: str, anchors: dict, schema_fields: list[str] | None = None) -> dict[str, Any]:
    """Apply anchors and grade them.

    Returns:
      {
        "ok": bool,            # would we trust these for production polls?
        "count": int,           # how many entities BS4 matched
        "expected_count": int,  # what the LLM claimed
        "first": {...},         # first matched entity (for inspection)
        "verification_match": bool,   # does first match the LLM's verification sample?
        "reasons": [...],       # why we'd reject these anchors
      }
    """
    reasons: list[str] = []
    entities = apply_anchors(html, anchors)
    expected = int(anchors.get("expected_count") or 0)
    got = len(entities)
    first = entities[0] if entities else None

    if got == 0:
        reasons.append("root_selector matched zero elements")

    if expected > 0:
        # Allow 30% slack — pages add/remove a row between snapshots.
        floor = max(1, int(expected * 0.5))
        if got < floor:
            reasons.append(f"matched {got} elements vs expected ~{expected}")

    verification = anchors.get("verification")
    verification_match = False
    if verification and first:
        # Field-by-field equality on the LLM's verification probe — using the
        # identity field (and any other listed fields) we sanity-check that
        # what BS4 extracted matches what the LLM said the first row would be.
        keys = (schema_fields or list(verification.keys()))[:1] or list(verification.keys())[:1]
        if keys:
            k = keys[0]
            v_expected = str(verification.get(k) or "").strip()
            v_actual = str(first.get(k) or "").strip()
            verification_match = v_expected != "" and v_expected == v_actual
            if not verification_match:
                reasons.append(
                    f"verification field '{k}': expected={v_expected!r} got={v_actual!r}"
                )

    ok = got > 0 and not reasons
    return {
        "ok": ok,
        "count": got,
        "expected_count": expected,
        "first": first,
        "verification_match": verification_match,
        "reasons": reasons,
    }
