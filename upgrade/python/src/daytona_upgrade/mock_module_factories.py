# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review replacement layout when a patch targets a lazy SDK module factory."""

from __future__ import annotations

import libcst as cst

from .attributes import AttributeContext
from .imports import mock_target_kind
from .mock_proofs import patch_helper
from .module_proofs import module_review


def review_module_factory(
    context: AttributeContext, original: cst.Call, target_index: int
) -> bool:
    """Reuse indexed string-target lookup for both dotted and module-object patches."""
    helper = patch_helper(context.kind(original.func))
    target = original.args[target_index].value
    name = ""
    # LibCST predicates retain the tool's Python 3.9 syntax floor.
    if helper in {"patch", "setattr"} and isinstance(
        target, (cst.SimpleString, cst.ConcatenatedString)
    ):
        literal = target.evaluated_value
        if isinstance(literal, str):
            name = literal
    elif helper in {"patch.object", "setattr"}:
        member = next(
            (
                arg.value
                for arg in original.args
                if arg.keyword is not None
                and arg.keyword.value
                == ("attribute" if helper == "patch.object" else "name")
            ),
            original.args[target_index + 1].value
            if target_index + 1 < len(original.args)
            else None,
        )
        kind = context.kind(target)
        if kind.startswith(("module:", "local-module:")) and isinstance(
            member, (cst.SimpleString, cst.ConcatenatedString)
        ):
            literal = member.evaluated_value
            if isinstance(literal, str):
                name = kind.split(":", 1)[1] + "." + literal
    if not name or any(arg.star for arg in original.args[:target_index]):
        return False
    kind = mock_target_kind(name, context.mapping, context.flow.context.index)
    if not kind.startswith("fn:") or not module_review(kind):
        return False
    if context.kind(original) == "local-double:module-patch":
        return True
    context.item(
        original,
        "mock-target",
        f"Mock target replaces `{name}`, which can return the daytona module; "
        "SDK 2.x reads 0.x paths under .v0, so give the replacement a v0 attribute "
        "(for example fake.v0.common.errors).",
    )
    return True
