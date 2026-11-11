# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The Python source files the tool reads and rewrites."""

from __future__ import annotations

from typing import Final

# Modules, stubs and windowed scripts (`.pyw`, which Windows also imports as
# modules, package initializers included).
PYTHON_SOURCE_SUFFIXES: Final = frozenset({".py", ".pyi", ".pyw"})
PACKAGE_INITIALIZERS: Final = tuple(
    sorted("__init__" + suffix for suffix in PYTHON_SOURCE_SUFFIXES)
)
