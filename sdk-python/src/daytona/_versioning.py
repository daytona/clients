# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# pyright: reportUnusedFunction=false
# pylint: disable=global-statement

from __future__ import annotations

import os
import sys
import threading
import warnings
from typing import Final

import pydantic

ROOT_NAMESPACE_WARNING_TEMPLATE: Final = "Use `daytona.v1.{member}` instead. The root form is removed in SDK 2.x."
TARGET_DEPRECATION_MESSAGE: Final = (
    "DaytonaConfig.target (and DAYTONA_TARGET) is deprecated and will be removed in a future version. "
    + "Pass target per sandbox create (CreateSandboxFromSnapshotParams.target / "
    + "CreateSandboxFromImageParams.target) or region_id per snapshot create "
    + "(CreateSnapshotParams.region_id) instead."
)

_root_namespace_warning_emitted = False
_target_warning_emitted = False
_lock = threading.Lock()

# Warnings are attributed to the first frame outside these packages, so they
# point at user code (Python shows DeprecationWarning by default only when it is
# attributed to __main__) however deep the SDK call path is.
_INTERNAL_DIRS: Final = (
    os.path.dirname(os.path.abspath(__file__)) + os.sep,
    os.path.dirname(os.path.abspath(pydantic.__file__)) + os.sep,
)


def root_namespace_message(member_name: str) -> str:
    return ROOT_NAMESPACE_WARNING_TEMPLATE.format(member=member_name)


def user_stacklevel() -> int:
    """Return the `warnings.warn` stacklevel of the first frame outside the SDK and pydantic."""
    # Level 1 is the function calling warnings.warn (and this helper).
    frame = sys._getframe(2)  # pylint: disable=protected-access
    level = 2
    while frame is not None and os.path.abspath(frame.f_code.co_filename).startswith(_INTERNAL_DIRS):
        frame = frame.f_back
        level += 1
    return level


def warn_root_namespace_once(member_name: str) -> None:
    global _root_namespace_warning_emitted

    with _lock:
        if _root_namespace_warning_emitted:
            return
        # Mark as emitted only once warn() returns: with warnings turned into errors, every call raises.
        warnings.warn(root_namespace_message(member_name), DeprecationWarning, stacklevel=user_stacklevel())
        _root_namespace_warning_emitted = True


def warn_target_deprecation_once() -> None:
    global _target_warning_emitted

    with _lock:
        if _target_warning_emitted:
            return
        warnings.warn(TARGET_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=user_stacklevel())
        _target_warning_emitted = True


def _reset_deprecation_warnings_for_tests() -> None:
    global _root_namespace_warning_emitted, _target_warning_emitted

    _root_namespace_warning_emitted = False
    _target_warning_emitted = False
