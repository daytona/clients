# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded element inference for local generators with strict branch joins."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, NamedTuple

from .collection_proofs import iterable_element
from .module_proofs import module_review

if TYPE_CHECKING:
    from .annotation_proofs import AnnotationProofs


class FunctionBindings(NamedTuple):
    """Decoration executes at definition; yielded expressions execute later."""

    definition: dict[str, str]
    execution: dict[str, str]


def function_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    runtime: dict[str, str],
    deferred: dict[ast.AST, set[str]],
) -> FunctionBindings:
    """Use execution-time bindings for inferred yields, definition-time annotations."""
    from .proof_mutations import invalidate

    env = runtime.copy()
    for name in deferred.get(node, set()):
        previous = env.get(name, "")
        invalidate(env, name)
        if review := module_review(previous):
            env[name] = review
    if "*" in deferred.get(node, set()):
        env = {
            name: module_review(kind)
            for name, kind in env.items()
            if module_review(kind)
        }
    return FunctionBindings(runtime, env)


def generator_kind(
    annotations: AnnotationProofs,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    runtime: dict[str, str],
) -> str:
    """Infer every supported yield without selecting branches or entering scopes."""
    # ScopeWrites imports Resolver; defer loading until modules are initialized.
    from ._scopes import ScopeWrites
    from .proof_mutations import invalidate

    if not any(
        isinstance(child, (ast.Yield, ast.YieldFrom)) for child in ast.walk(node)
    ):
        return ""
    writes = ScopeWrites()
    writes.generic_visit(node)
    env = runtime.copy()
    for name, _ in writes.events:
        invalidate(env, name)
        env[name] = ""
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    for argument in arguments:
        invalidate(env, argument.arg)
        env[argument.arg] = annotations.resolve(argument.annotation)
    for argument in (node.args.vararg, node.args.kwarg):
        if argument:
            invalidate(env, argument.arg)
            env[argument.arg] = ""
            if argument is node.args.vararg:
                kind = annotations.element(argument.annotation)
                env[argument.arg] = "items:" + kind if kind else ""
    kinds = _yield_kinds(annotations, node.body, env.copy())
    if kinds is None:
        return _possible_client_yields(annotations, node.body, env)
    if not kinds or not any(kind in {"client", "unproven:client"} for kind in kinds):
        return ""
    element = "client" if all(kind == "client" for kind in kinds) else "unproven:client"
    return "items:" + element


def _executed_children(node: ast.AST) -> list[ast.AST]:
    """Definition-time expressions execute here; function/lambda bodies do not."""
    children = list(ast.iter_child_nodes(node))
    # AST predicates retain compatibility with the Python 3.9 syntax floor.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [child for child in children if child not in node.body]
    if isinstance(node, ast.Lambda):
        return [child for child in children if child is not node.body]
    return children


def _possible_client_yields(
    annotations: AnnotationProofs, statements: list[ast.stmt], env: dict[str, str]
) -> str:
    """Retain possible evidence only; this scan never establishes exact flow proof."""
    resolver = annotations.resolver
    pending: list[ast.AST] = list(reversed(statements))
    nodes: list[ast.AST] = []
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(reversed(_executed_children(node)))
    # This is a bounded may-analysis: client evidence survives conflicting writes.
    # Repeated passes permit aliases assigned inside try/if bodies, not execution.
    for _ in range(32):
        previous = env.copy()
        for node in nodes:
            bindings: list[tuple[str, str]] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                bindings = [
                    (
                        alias.asname or alias.name,
                        resolver.imported(node.module, alias.name),
                    )
                    for alias in node.names
                ]
            elif isinstance(node, ast.Import):
                bindings = [
                    (
                        alias.asname or alias.name.split(".")[0],
                        "module:"
                        + (alias.name if alias.asname else alias.name.split(".")[0]),
                    )
                    for alias in node.names
                ]
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            ):
                kind = resolver.infer(node.value, env)
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                bindings = [
                    (target.id, kind)
                    for target in targets
                    if isinstance(target, ast.Name)
                ]
            for name, kind in bindings:
                if kind and (
                    not env.get(name)
                    or kind == "client"
                    or kind.endswith(":client")
                    or kind.startswith(
                        tuple("module:" + module for module in resolver.mapping.modules)
                    )
                ):
                    env[name] = kind
        if env == previous:
            break
    for node in nodes:
        if isinstance(node, (ast.Yield, ast.YieldFrom)) and node.value is not None:
            kind = resolver.infer(node.value, env)
            if isinstance(node, ast.YieldFrom):
                kind = iterable_element(kind)
            if kind in {"client", "unproven:client"}:
                return "items:unproven:client"
    return ""


def _yield_kinds(
    annotations: AnnotationProofs, statements: list[ast.stmt], env: dict[str, str]
) -> list[str] | None:
    """Accumulate yields while mutating only the current path's local environment."""
    from .proof_mutations import invalidate

    resolver = annotations.resolver
    kinds: list[str] = []
    for statement in statements:
        if isinstance(statement, ast.Expr) and isinstance(
            statement.value, (ast.Yield, ast.YieldFrom)
        ):
            value = statement.value.value
            kind = resolver.infer(value, env) if value is not None else "non-client"
            kinds.append(
                iterable_element(kind)
                if isinstance(statement.value, ast.YieldFrom)
                else kind
            )
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            # A yield expression used as an assignment receives a caller-sent value.
            if value is not None and any(
                isinstance(child, (ast.Yield, ast.YieldFrom, ast.NamedExpr))
                for child in ast.walk(value)
            ):
                return None
            kind = resolver.infer(value, env) if value is not None else ""
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if not all(isinstance(target, ast.Name) for target in targets):
                return None
            for target in targets:
                if isinstance(target, ast.Name):
                    invalidate(env, target.id)
                    env[target.id] = kind
        elif isinstance(statement, ast.If):
            # Suspensions in the test yield elements too; nested scopes are bounded out.
            condition_nodes = list(ast.walk(statement.test))
            if any(
                isinstance(child, (ast.NamedExpr, ast.Lambda))
                for child in condition_nodes
            ):
                return None
            condition_yields: list[ast.stmt] = [
                ast.Expr(value=child)
                for child in condition_nodes
                if isinstance(child, (ast.Yield, ast.YieldFrom))
            ]
            if condition_yields:
                yielded = _yield_kinds(annotations, condition_yields, env)
                if yielded is None:
                    return None
                kinds.extend(yielded)
            branches = [env.copy(), env.copy()]
            for body, branch in zip((statement.body, statement.orelse), branches):
                yielded = _yield_kinds(annotations, body, branch)
                if yielded is None:
                    return None
                kinds.extend(yielded)
            env.clear()
            for name in set(branches[0]) | set(branches[1]):
                left, right = (branch.get(name, "") for branch in branches)
                env[name] = (
                    left
                    if left == right
                    else "unproven:client"
                    if {left, right} & {"client", "unproven:client"}
                    else ""
                )
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                statement.decorator_list
                or statement.args.defaults
                or any(statement.args.kw_defaults)
                or any(
                    isinstance(child, (ast.Yield, ast.YieldFrom))
                    for expression in _executed_children(statement)
                    for child in ast.walk(expression)
                )
            ):
                return None
        elif isinstance(statement, ast.Pass):
            continue
        elif isinstance(statement, ast.Return):
            break
        elif isinstance(statement, ast.Expr) and isinstance(
            statement.value, ast.Constant
        ):
            continue
        else:
            return None
    return kinds
