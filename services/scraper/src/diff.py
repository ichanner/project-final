"""id derivation for diffing extracted entities against db"""

from __future__ import annotations

import json
from typing import Any


def identity_for(
    entity: dict[str, Any],
    identity_key: list[str],
    schema_fields: list[str] | None = None,
) -> str:
    if identity_key:
        return "||".join(str(entity.get(k, "")).strip() for k in identity_key)
    if schema_fields:
        first = schema_fields[0]
        val = entity.get(first)
        if val is not None and str(val).strip():
            return str(val).strip()
    return json.dumps(entity, sort_keys=True, default=str)
