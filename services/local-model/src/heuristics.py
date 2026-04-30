"""Heuristic structured-data extractor.

Stands in for a distilled local model. Returns the same shape extracto returns,
plus a confidence score that drops to ~0 on ambiguous pages so the orchestrator
can decide to escalate to the cloud path.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _entities_from_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and "@graph" in item:
                items.extend(item["@graph"])
        for item in items:
            if not isinstance(item, dict):
                continue
            if "@context" in item or "@type" in item:
                cleaned = {
                    k: v
                    for k, v in item.items()
                    if not k.startswith("@") and not isinstance(v, dict | list)
                }
                if cleaned:
                    out.append(cleaned)
    return out


def _entities_from_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Pick the largest table that has a header row."""
    best: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        headers = [_normalize(th.get_text()) for th in table.find_all("th")]
        if not headers:
            continue
        rows: list[dict[str, Any]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td"])
            if len(cells) != len(headers):
                continue
            rows.append({headers[i]: _normalize(c.get_text()) for i, c in enumerate(cells)})
        if len(rows) > len(best):
            best = rows
    return best


def _entities_from_repeating_cards(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Coarse: look for an element with many similarly-classed children."""
    candidates: list[tuple[int, list[dict[str, Any]]]] = []
    for parent in soup.find_all(["ul", "ol", "div", "section"]):
        children = parent.find_all(recursive=False)
        if len(children) < 4:
            continue
        # Need at least 4 children with the same tag and overlapping classes.
        first = children[0]
        same_tag = sum(1 for c in children if c.name == first.name)
        if same_tag < 4:
            continue
        first_classes = set(first.get("class") or [])
        if not first_classes:
            continue
        if sum(1 for c in children if first_classes & set(c.get("class") or [])) < 4:
            continue
        rows = []
        for c in children:
            title = c.find(["h1", "h2", "h3", "h4", "a"])
            if title is None:
                continue
            href = title.get("href") if title.name == "a" else None
            rows.append(
                {
                    "title": _normalize(title.get_text()),
                    "href": href,
                    "text": _normalize(c.get_text())[:240],
                }
            )
        if rows:
            candidates.append((len(rows), rows))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    return candidates[0][1]


def _coerce_to_schema(entities: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    fields = schema.get("fields") if isinstance(schema, dict) else None
    if not isinstance(fields, dict) or not fields:
        return entities
    coerced = []
    for ent in entities:
        out = {}
        # Case-insensitive lookup of source keys.
        lower = {k.lower(): v for k, v in ent.items()}
        for field_name in fields:
            v = lower.get(field_name.lower())
            out[field_name] = v if v is not None else None
        # Drop entries where every field is None — those aren't real matches.
        if any(v is not None for v in out.values()):
            coerced.append(out)
    return coerced


def extract(html: str, schema: dict[str, Any] | None, anchor: str | None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    sources = [
        ("jsonld", _entities_from_jsonld(soup)),
        ("table", _entities_from_table(soup)),
        ("cards", _entities_from_repeating_cards(soup)),
    ]
    sources = [(label, ents) for label, ents in sources if ents]

    if not sources:
        return {"backend": "local", "entities": [], "confidence": 0.0, "source": None}

    # Prefer the source with the most entities.
    sources.sort(key=lambda x: len(x[1]), reverse=True)
    label, entities = sources[0]

    coerced = _coerce_to_schema(entities, schema or {})

    # Confidence heuristic:
    # - JSON-LD: high baseline (sites publish it intentionally)
    # - Table with schema match: medium-high
    # - Cards: medium (often contains noise)
    # - Schema coercion that drops most entities -> dampen
    base = {"jsonld": 0.85, "table": 0.78, "cards": 0.55}[label]
    if coerced and entities:
        retention = len(coerced) / len(entities)
        confidence = base * (0.5 + 0.5 * retention)
    else:
        confidence = base * 0.4 if not coerced else base
    if anchor and label == "cards":
        # Without semantic understanding, anchor-driven extraction is risky.
        confidence *= 0.7

    return {
        "backend": "local",
        "entities": coerced or entities,
        "confidence": round(min(1.0, confidence), 3),
        "source": label,
    }
