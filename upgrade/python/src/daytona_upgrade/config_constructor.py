# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review client constructors whose config URL ownership cannot be proven."""

from __future__ import annotations

from typing import TYPE_CHECKING

import libcst as cst

from .config_messages import SHARED_URL_WRITE, UNRESOLVED_CONFIG, UNRESOLVED_URL_FIELDS
from .config_readers import visible_url_write
from .subclasses import (
    client_constructor,
    owns_config_constructor,
    safe_config_constructor,
)

if TYPE_CHECKING:
    from .calls import CallContext


def review_constructor(context: CallContext, original: cst.Call) -> None:
    """Keep unverified config calls intact while making runtime refusal visible."""
    binding = context.kind(original.func)
    if not (
        client_constructor(binding)
        and original.args
        and not owns_config_constructor(binding)
    ):
        return
    if (
        len(original.args) == 1
        and not original.args[0].star
        and safe_config_constructor(binding)
        and (
            isinstance(original.args[0].value, cst.Name)
            and original.args[0].value.value == "None"
            or context.config_origins.resolved(context.position(original.args[0].value))
        )
    ):
        return
    if context.options.drop_api_url:
        retained_write = any(
            visible_url_write(context.config_origins, context.position(arg.value))
            for arg in original.args
        )
        context.fixme(
            original, SHARED_URL_WRITE if retained_write else UNRESOLVED_URL_FIELDS
        )
    context.item(
        original,
        "config-field" if context.options.drop_api_url else "custom-api-url",
        "this DaytonaConfig's API URL could not be verified; --drop-api-url keeps the call as is — confirm it targets Daytona Cloud"
        if context.options.drop_api_url
        else UNRESOLVED_CONFIG,
    )
