"""Identity-keyed diffing of extracted entities against the DB."""

from __future__ import annotations

import json
from typing import Any


def identity_for(entity: dict[str, Any], identity_key: list[str]) -> str:
    """Stable identity string from configured key fields."""
    if not identity_key:
        return json.dumps(entity, sort_keys=True, default=str)
    parts = [str(entity.get(k, "")).strip() for k in identity_key]
    return "||".join(parts)
