# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Rewrite nullable references in a down-converted spec to the OpenAPI 3.0 form.

gen-contract.sh runs this on the ephemeral OpenAPI 3.0 copy, never on
contract/. The 3.1 contract spells a nullable reference
``oneOf: [{type: null}, {$ref: X}]`` and the down-converter keeps that
shape. openapi-generator flags such a property nullable on the schema that
declares it, but loses the flag when another schema inherits the property
through ``allOf``: the inheriting model's ``to_dict()`` then drops an explicit
``null`` it received. Rewritten to ``{allOf: [{$ref: X}], nullable: true}``,
the property is nullable in every model that has it, declared or inherited.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Union

Json = Union[None, bool, int, float, str, List["Json"], Dict[str, "Json"]]

NULL_SCHEMA: Dict[str, Json] = {"type": "null"}
# Keys a reference may carry next to ``$ref``; OpenAPI 3.0 ignores them there,
# so the rewrite moves them onto the property.
ANNOTATIONS = frozenset({"description", "title"})


def _nullable_ref(members: Json) -> Union[Dict[str, Json], None]:
    """The ``$ref`` member of ``[{type: null}, {$ref: X}]`` (either order), else None."""
    if not isinstance(members, list) or len(members) != 2 or NULL_SCHEMA not in members:
        return None
    other = members[1] if members[0] == NULL_SCHEMA else members[0]
    if (
        isinstance(other, dict)
        and isinstance(other.get("$ref"), str)
        and set(other) - {"$ref"} <= ANNOTATIONS
    ):
        return other
    return None


def normalize(node: Json) -> int:
    """Rewrite ``node`` in place; return the number of schemas rewritten."""
    count = 0
    if isinstance(node, dict):
        for key in ("oneOf", "anyOf"):
            ref = _nullable_ref(node.get(key))
            if ref is not None and "allOf" not in node:
                del node[key]
                for annotation in sorted(set(ref) & ANNOTATIONS):
                    node.setdefault(annotation, ref[annotation])
                node["allOf"] = [{"$ref": ref["$ref"]}]
                node["nullable"] = True
                count += 1
                break
        for value in node.values():
            count += normalize(value)
    elif isinstance(node, list):
        for value in node:
            count += normalize(value)
    return count


def main(argv: List[str]) -> int:
    for arg in argv:
        path = Path(arg)
        spec = json.loads(path.read_text())
        normalize(spec)
        path.write_text(json.dumps(spec, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
