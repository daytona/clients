# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fixed-point project import indexing and shared config preflight."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from .analysis_filter import plan_analysis
from .annotation_exports import resolved_exports
from .models import Mapping
from .project_configs import inspect_configs
from .project_dependencies import ExportReads
from .pytest_collection import PytestCollection
from .resolution import ProjectIndex, Resolver
from .wildcard_exports import declared_exports


@dataclass(frozen=True)
class ProjectLayout:
    """Module locations and the scan root's collection rules."""

    files: dict[str, str]
    collection: PytestCollection = PytestCollection()
    progress: Callable[[str], None] | None = None


def build_index(
    sources: dict[str, str],
    mapping: Mapping,
    files: dict[str, str] | ProjectLayout | None = None,
) -> ProjectIndex:
    """Reach a fixed point over project imports, client globals and factory summaries."""
    from .factory_consumers import factory_consumers
    from .factory_patch_scopes import namespace_calls
    from .flow import Flow, SourceContext

    layout = files if isinstance(files, ProjectLayout) else ProjectLayout(files or {})
    plan = plan_analysis(sources, mapping, layout)
    skipped = frozenset(
        (module, source)
        for module, source in sources.items()
        if module not in plan.modules
    )
    wildcard_exports = {
        module: names
        for module, source in sources.items()
        if (names := declared_exports(source)) is not None
    }
    exports: dict[str, dict[str, str]] = {module: {} for module in sources}
    index = ProjectIndex(
        exports,
        wildcard_exports=wildcard_exports,
        collection=layout.collection,
        skipped_sources=skipped,
    )
    flows: dict[str, Flow] = {}
    order = {module: number for number, module in enumerate(sources)}
    modules = plan.modules & plan.syntax.keys()
    pending = set(modules)
    dependents: dict[str, set[str]] = {}
    iteration = 0
    while pending:
        iteration += 1
        updates: dict[str, dict[str, str]] = {}
        mutations = index.class_mutations
        links = set(index.parameter_links)
        # Keep each wave synchronous, as before: a changed proof must not affect
        # another module until the next wave. Only dependent modules need it.
        for number, module in enumerate(sorted(pending, key=order.__getitem__), 1):
            source = sources[module]
            file = layout.files.get(module, module)
            if layout.progress and (number == 1 or number % 50 == 0):
                layout.progress(
                    f"Indexing pass {iteration}: {number}/{len(pending)} {file}"
                )
            reads = ExportReads(exports)
            observed = replace(index, exports=reads)
            flow = Flow(
                Resolver(mapping, observed), SourceContext(file, module, observed)
            )
            try:
                flow.analyze(source, plan.syntax[module])
            except SyntaxError:
                updates[module] = {}
                flows.pop(module, None)
            else:
                updates[module] = resolved_exports(flow)
                flows[module] = flow
                mutations |= flow.owner_mutations
                links.update(flow.parameter_links)
            for dependency in reads.modules:
                dependents.setdefault(dependency, set()).add(module)
        pending = {
            dependent
            for module, summary in updates.items()
            if summary != exports[module]
            for dependent in dependents.get(module, ())
        }
        if mutations != index.class_mutations:
            # Every Flow reads the global mutation fence, independently of imports.
            pending.update(modules)
        pending.update(
            module
            for module, _, _ in links - index.parameter_links
            if module in modules
        )
        exports.update(updates)
        index = replace(
            index, class_mutations=mutations, parameter_links=frozenset(links)
        )
    # Retained flows may predate the last wave. Config preflight needs the final
    # index for any additional inference, not their earlier observation wrappers.
    for flow in flows.values():
        flow.resolver = Resolver(mapping, index)
        flow.context = replace(flow.context, index=index)
    consumers = factory_consumers(flows)
    return inspect_configs(
        replace(
            index,
            adaptable_factories=consumers.adaptable,
            namespace_factories=consumers.namespace,
            namespace_calls=namespace_calls(flows),
        ),
        flows,
    )
