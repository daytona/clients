# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Parse source inputs and orchestrate the position-preserving CST transform."""

from __future__ import annotations

import ast

import libcst as cst
from libcst.metadata import MetadataWrapper

from .comments import remove_suggestions
from .constructor_errors import constructor_guidance
from .flow import Flow, SourceContext
from .local_doubles import prove_local_doubles
from .manifest_errors import python_source_item
from .models import SourceResult, TransformOptions
from .resolution import Resolver


def transform_source(
    source: str, options: TransformOptions, context: SourceContext
) -> SourceResult:
    from .transform import Upgrade

    if options.mode == "remove-suggestions":
        cleaned = remove_suggestions(source)
        return SourceResult(cleaned, (), "daytona" in source)
    flow = Flow(Resolver(options.mapping, context.index), context)
    try:
        module, parsed = cst.parse_module(source), ast.parse(source)
        if (context.module, source) in context.index.skipped_sources:
            return SourceResult(source, (), False)
        flow.analyze(source, parsed)
        double_items = prove_local_doubles(flow)
    except (SyntaxError, cst.ParserSyntaxError) as error:
        item = python_source_item(context.file, error)
        return SourceResult(source, (item,), "daytona" in source)
    visitor = Upgrade(flow, options, module.default_newline)
    if options.mode == "upgrade":
        visitor.items.extend(double_items)
    result = MetadataWrapper(module).visit(visitor)
    if options.mode == "upgrade":
        result, items = constructor_guidance(flow, result, visitor.constructors)
        visitor.items.extend(items)
    return SourceResult(
        result.code, tuple(visitor.items), flow.found or visitor.sdk_mock_found
    )
