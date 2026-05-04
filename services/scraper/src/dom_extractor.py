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


def apply_anchors(
    html: str,
    anchors: dict,
    identity_field: str | None = None,
) -> list[dict[str, Any]]:
    
    
    if not anchors or not anchors.get("root_selector"):
        return []
    soup = BeautifulSoup(html, "lxml")
    root_nodes = soup.select(anchors["root_selector"])
    fields: dict[str, dict] = anchors.get("fields") or {}
    if identity_field is None and fields:
        identity_field = next(iter(fields))

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for root in root_nodes:
        entity: dict[str, Any] = {}
        for field_name, recipe in fields.items():
            sel = recipe.get("selector")
            child = root.select_one(sel) if sel else root
            raw = _apply_extract(child, recipe.get("extract", "text"))
            entity[field_name] = _apply_transform(raw, recipe.get("transform"))
        if identity_field:
            id_val = entity.get(identity_field)
            id_str = (str(id_val).strip() if id_val is not None else "")
            if id_str:
                if id_str in seen_ids:
                    continue
                seen_ids.add(id_str)
        out.append(entity)
    return out


def verify_anchors(html: str, anchors: dict, schema_fields: list[str] | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    entities = apply_anchors(html, anchors)
    expected = int(anchors.get("expected_count") or 0)
    got = len(entities)
    first = entities[0] if entities else None

    if got == 0:
        reasons.append("root_selector matched zero elements")

    if expected > 0:
        floor = max(1, int(expected * 0.5))
        if got < floor:
            reasons.append(f"matched {got} elements vs expected ~{expected}")

    verification = anchors.get("verification")
    verification_match = False
    if verification and first:
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
