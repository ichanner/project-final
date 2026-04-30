"""Identity derivation for diffing extracted entities against the DB."""

from __future__ import annotations

import json
from typing import Any


def identity_for(
    entity: dict[str, Any],
    identity_key: list[str],
    schema_fields: list[str] | None = None,
) -> str:
    """Stable identity string for an entity.

    Resolution order:
      1. Explicit identity_key (joined with "||"), if provided.
      2. The first field of the schema, if available — this is the implicit
         identity used by the new schema-builder UI which doesn't ask the
         user to pick a key.
      3. JSON-stringified entity (every field change = new entity).
    """
    if identity_key:
        return "||".join(str(entity.get(k, "")).strip() for k in identity_key)
    if schema_fields:
        first = schema_fields[0]
        val = entity.get(first)
        if val is not None and str(val).strip():
            return str(val).strip()
    return json.dumps(entity, sort_keys=True, default=str)
