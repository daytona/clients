# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Prove that a create call passes a 0.x Dockerfile image as its image parameter."""

from __future__ import annotations

import ast
from typing import Callable

from .models import Mapping

# The kind of a value proven to be the result of the 0.x ``Image.from_dockerfile(...)``.
DOCKERFILE_IMAGE = "v0-dockerfile-image"


def is_dockerfile_image_call(function: str, mapping: Mapping) -> bool:
    """Whether ``function`` (an inferred callee) is the 0.x Dockerfile image call.

    The callee is the 0.x ``Image`` class imported from a 0.x module (before or
    after the default pass moves it to ``daytona.v0``), never from ``daytona.v2``.
    """
    proof = mapping.dockerfile_image
    if proof is None or not function.startswith("module:"):
        return False
    suffix = "." + proof.type + "." + proof.method
    if not function.endswith(suffix):
        return False
    module = function[len("module:") : -len(suffix)]
    return any(module in (root, root + ".v0") for root in mapping.modules)


def passes_dockerfile_image(
    node: ast.Call, mapping: Mapping, infer: Callable[[ast.AST], str]
) -> bool:
    """Whether the call's parameters (its first argument, or ``params=``) are a
    constructor call whose image keyword provably is a 0.x Dockerfile image.

    A ``**`` argument could replace the keyword, so it defeats the proof.
    """
    proof = mapping.dockerfile_image
    if proof is None:
        return False
    params = (
        node.args[0]
        if node.args
        else next(
            (keyword.value for keyword in node.keywords if keyword.arg == "params"),
            None,
        )
    )
    if not isinstance(params, ast.Call) or any(
        keyword.arg is None for keyword in params.keywords
    ):
        return False
    images = [
        keyword.value for keyword in params.keywords if keyword.arg == proof.parameter
    ]
    return len(images) == 1 and infer(images[0]) == DOCKERFILE_IMAGE
