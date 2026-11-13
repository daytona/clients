# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Build tagged, replaceable v2 guidance from receiver proofs and shared data."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ._workflow_index import WorkflowIndex
from .flow import Call, Flow
from .lexical import single_line
from .models import Item, Mapping, SuggestionReport
from .optional_arguments import call_argument, render_optional_arguments
from .persistent_workflow import persistent_lifecycle_calls
from .resolution import dotted
from .shared_error_guidance import shared_error_imports
from .suggestion_owners import ServiceOwners


@dataclass(frozen=True)
class Guidance:
    comments: dict[int, list[str]]
    items: tuple[Item, ...]


def guidance(flow: Flow, mapping: Mapping) -> Guidance:
    owners = ServiceOwners(flow)
    index = WorkflowIndex(flow, owners)
    comments: dict[int, list[str]] = {}
    headers: dict[int, list[str]] = {}
    seen: dict[int, set[tuple[str, int, int]]] = {}
    workflows: set[tuple[int, int, str]] = set()
    assignments: dict[int, list[ast.Assign | ast.AnnAssign]] = {}
    for node in index.scopes.nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            assignments.setdefault(node.lineno, []).append(node)
    items: list[Item] = []
    for node, error_names in shared_error_imports(flow, index.scopes):
        message = "when moving these calls to v2, import shared errors from daytona so except clauses also catch v2 errors; root errors take only a message and have no 0.x attributes"
        aliases = sorted(
            name for name in error_names if name in mapping.root_alias_categories
        )
        if aliases:
            message += "; " + ", ".join(
                f"{alias} is {mapping.root_alias_categories[alias]} at the root"
                for alias in aliases
            )
        comments.setdefault(node.lineno, []).append("daytona-v2: " + message)
        items.append(
            Item(
                file=flow.context.file,
                line=node.lineno,
                column=node.col_offset + 1,
                kind="suggestion",
                message=message,
            )
        )
    for call in index.suggestion_calls():
        if call.kind == "unproven:client" and call.method in mapping.moved:
            receiver = single_line(call.receiver)
            message = f"if `{receiver}` is a Daytona client, use `{receiver}.v0.{call.method}(...)` (SDK 2.x moved client methods to .v0)"
            comments.setdefault(call.statement, []).append(
                "FIXME(daytona-upgrade): " + message
            )
            items.append(
                Item(
                    file=flow.context.file,
                    line=call.node.lineno,
                    column=call.node.col_offset + 1,
                    kind="fixme",
                    message=message,
                )
            )
            continue
        entry = next(
            (
                s
                for s in mapping.suggestions
                if s.receiver == call.kind.removesuffix(".v0")
                and s.method == call.method
            ),
            None,
        )
        if entry is None:
            continue
        emitted = seen.setdefault(call.statement, set())
        key = (entry.id, call.node.lineno, call.node.col_offset)
        if key in emitted:
            continue
        emitted.add(key)
        client, sandbox = (single_line(value) for value in receivers(call, owners))
        # A 0.x Dockerfile image needs its build context: the row's own form for it.
        dockerfile = call.dockerfile_image and entry.dockerfile_form is not None
        entry_form = entry.dockerfile_form if dockerfile else entry.form
        entry_notes = (entry.dockerfile_notes or ()) if dockerfile else entry.notes
        category = "differs" if dockerfile else entry.category
        form = (
            owners.render(
                render_optional_arguments(
                    entry_form,
                    lambda name, position, node=call.node: _single(
                        call_argument(node, name, position, flow.text.segment)
                    ),
                ),
                call,
            )
            .replace("{client}", client)
            .replace("{sandbox}", sandbox)
            if entry_form
            else None
        )
        notes = [
            owners.render(note, call)
            .replace("{client}", client)
            .replace("{sandbox}", sandbox)
            for note in entry_notes
        ]
        docs = mapping.docs + "#" + entry.anchor
        manual_client = (
            call.kind.startswith("client.")
            and (call.node.lineno, call.node.col_offset) not in owners.clients
        )
        # A handle a sandbox call produced (a PTY) is not its
        # sandbox: an unproven owner needs the same manual move as a service.
        sandbox_produced = owners.sandbox_produced(entry.receiver)
        manual = manual_client or bool(
            entry.form
            and (call.kind.startswith("sandbox.") or sandbox_produced)
            and "{sandbox}" in owners.render(entry.form, call)
        )
        message = (
            "move this call to the owning root Daytona client: " + form
            if manual_client and form
            else "move this call to the owning sandbox: " + form
            if manual and form
            else form or "No v2 equivalent"
        )
        block = comments.setdefault(call.statement, [])
        block.append(
            "daytona-v2: manual: " + message
            if manual_client
            else "FIXME(daytona-upgrade): " + message
            if manual
            else "daytona-v2: " + (form or "none: No v2 equivalent")
        )
        if category != "direct":
            block.extend(
                f"daytona-v2:   {'none' if category == 'none' else 'differs'}: {note}"
                for note in notes
            )
        block.append("daytona-v2:   docs: " + docs)
        suggestion: SuggestionReport = {
            "id": entry.id,
            "receiver": entry.receiver,
            "v0": call.receiver + "." + call.method,
            "v2": form,
            "category": category,
            "notes": notes,
            "docs": docs,
        }
        items.append(
            {
                "file": flow.context.file,
                "line": call.node.lineno,
                "column": call.node.col_offset + 1,
                "kind": "fixme" if manual else "suggestion",
                "message": message,
                "suggestion": suggestion,
            }
        )
        if entry.starts_workflow and call.binding:
            position = (call.node.lineno, call.node.col_offset)
            column = next(
                (
                    node.col_offset
                    for node in assignments.get(call.statement, [])
                    if (node.lineno, node.col_offset)
                    <= position
                    < (node.end_lineno or node.lineno, node.end_col_offset or 0)
                ),
                call.node.col_offset,
            )
            workflow = (call.statement, column, call.binding)
            if workflow in workflows:
                continue
            workflows.add(workflow)
            methods = workflow_methods(call, index)
            persistent = persistent_lifecycle_calls(call, index, owners)
            header = (
                f"daytona-v2: workflow `{call.binding}`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: {', '.join(persistent)}"
                if persistent
                else f"daytona-v2: workflow `{call.binding}`: move these v0 calls on it to v2 together: {', '.join(methods)}"
                if methods
                else f"daytona-v2: workflow `{call.binding}`: move this sandbox to v2 as a unit"
            )
            headers.setdefault(call.statement, []).extend(
                [
                    header,
                    "daytona-v2:   data: " + "; ".join(mapping.data_traps),
                    "daytona-v2:   docs: " + mapping.docs + "#data",
                ]
            )
            items.append(
                {
                    "file": flow.context.file,
                    "line": call.node.lineno,
                    "column": call.node.col_offset + 1,
                    "kind": "workflow",
                    "message": header.removeprefix("daytona-v2: "),
                    "suggestion": {
                        **suggestion,
                        "id": f"workflow:{call.binding}",
                        "notes": list(mapping.data_traps),
                        "docs": mapping.docs + "#data",
                    },
                }
            )
    return Guidance(
        {line: [*headers.get(line, []), *block] for line, block in comments.items()},
        tuple(items),
    )


def receivers(call: Call, owners: ServiceOwners) -> tuple[str, str]:
    if call.kind.startswith("client"):
        owner = owners.clients.get((call.node.lineno, call.node.col_offset))
        if owner:
            return owner, call.binding or "sandbox"
        if call.kind != "client":
            return "root_client", call.binding or "sandbox"
        return call.receiver, call.binding or "sandbox"
    if call.kind == "sandbox":
        return "daytona", call.receiver
    return "daytona", "sandbox"


def workflow_methods(producer: Call, flow: Flow | WorkflowIndex) -> list[str]:
    """Methods on this workflow, including deferred calls capturing its binding."""
    result: list[str] = []
    index = (
        flow
        if isinstance(flow, WorkflowIndex)
        else WorkflowIndex(flow, ServiceOwners(flow))
    )
    owners = index.owners
    for call in index.workflow_calls(producer):
        position = (call.node.lineno, call.node.col_offset)
        if call.kind.startswith("sandbox") and (
            call.receiver == producer.binding
            or call.receiver.startswith(producer.binding + ".")
            or owners.sandboxes.get(position) == producer.binding
        ):
            attributes: list[str] = []
            kind = call.kind
            while kind.startswith("sandbox."):
                definition = index.flow.resolver.mapping.receivers[kind]
                if definition.attr:
                    attributes.insert(0, definition.attr)
                kind = definition.parent or ""
            method = ".".join(attributes)
            name = (method + "." if method else "") + call.method
        elif (call.kind == "client" or call.kind.startswith("client.")) and any(
            dotted(argument) == producer.binding
            or owners.argument_owner(argument) == producer.binding
            for argument in [
                *call.node.args,
                *(keyword.value for keyword in call.node.keywords),
            ]
        ):
            name = single_line(index.flow.text.segment(call.node.func) or "")
        else:
            continue
        if name not in result:
            result.append(name)
    return result


def _single(text: str | None) -> str | None:
    return None if text is None else single_line(text)
