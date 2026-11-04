# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review factory installations independently of how their mocks are configured."""

from __future__ import annotations

import ast

from .client_double_targets import ClientDoubleTargets
from .factory_patch_scopes import patch_reaches_factory


def factory_patch_sites(
    targets: ClientDoubleTargets, accepted: set[ast.expr]
) -> dict[ast.expr, ast.Call]:
    """A changed result contract needs either an adapted replacement or a patch item.

    Default mocks, later return_value assignments and opaque side effects have no
    bounded replacement graph. They still replace the same indexed declaration.
    """
    sites: dict[ast.expr, ast.Call] = {}
    for node in ast.walk(targets.flow.module):
        if not isinstance(node, ast.Call):
            continue
        target = targets.target(node)
        if target is None:
            continue
        kind, contract, _ = target
        if (
            kind != "fn:client"
            or contract not in targets.flow.context.index.namespace_factories
            or not patch_reaches_factory(node, contract, targets)
        ):
            continue
        patch = targets.patch(node)
        expected = {patch[0]: patch[1]} if patch is not None else {}
        while True:
            before = len(expected)
            targets.expand(expected)
            if len(expected) == before:
                break
        clients = {
            value
            for value, expected_kind in expected.items()
            if expected_kind == "client"
        }
        competing = (
            sum(
                kw.arg in {"new", "return_value", "side_effect"} for kw in node.keywords
            )
            > 1
        )
        if (
            clients
            and not competing
            and all(
                value in accepted
                or targets.kind(value)
                in {"client", "client.v0", "mock:client", "mock:async-client"}
                for value in clients
            )
        ):
            continue
        sites.update((value, node) for value in expected)
        sites[node] = node
    return sites
