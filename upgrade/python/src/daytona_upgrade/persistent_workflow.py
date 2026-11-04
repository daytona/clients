# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Persistent sandbox lifecycle checks for workflow guidance."""

from __future__ import annotations

from ._workflow_index import WorkflowIndex
from .flow import Call, Flow
from .lexical import single_line
from .resolution import dotted
from .suggestion_owners import ServiceOwners


def persistent_lifecycle_calls(
    producer: Call, flow: Flow | WorkflowIndex, owners: ServiceOwners
) -> list[str]:
    """Ordered persistence-dependent methods in this workflow's binding window."""
    index = flow if isinstance(flow, WorkflowIndex) else WorkflowIndex(flow, owners)
    lifecycle = {
        "start",
        "stop",
        "pause",
        "archive",
        "set_autostop_interval",
        "set_auto_pause_interval",
        "set_auto_archive_interval",
    }
    result: list[str] = []
    for call in index.workflow_calls(producer):
        position = (call.node.lineno, call.node.col_offset)
        if call.kind == "sandbox" and (
            call.receiver == producer.binding
            or owners.sandboxes.get(position) == producer.binding
        ):
            if call.method in lifecycle:
                if call.method not in result:
                    result.append(call.method)
        if call.kind in {"client", "client.v0"} and call.method in {"start", "stop"}:
            if any(
                dotted(argument) == producer.binding
                or owners.argument_owner(argument) == producer.binding
                for argument in [
                    *call.node.args,
                    *(keyword.value for keyword in call.node.keywords),
                ]
            ):
                name = single_line(index.flow.text.segment(call.node.func) or "")
                if name not in result:
                    result.append(name)
    return result
