# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative Daytona SDK migration, without executing customer code."""

from importlib.metadata import PackageNotFoundError, version
from typing import Final

try:
    _installed_version = version("daytona-upgrade")
except PackageNotFoundError:
    _installed_version = "0.0.0"

__version__: Final = _installed_version
