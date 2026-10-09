# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Versioned alias of the Daytona SDK API.

`daytona.v1` is a versioned alias of the current client API, for code that wants to pin the API
version explicitly. It re-exports every public name of the top-level `daytona` package, so the
two imports below refer to the same objects:

```python
from daytona import Daytona, Sandbox
from daytona.v1 import Daytona, Sandbox
```
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import daytona as _root

    # Static re-export of the root API for type checkers; at runtime names resolve lazily below.
    from . import *  # noqa: F403  # pylint: disable=wildcard-import
else:
    # Resolved relative to this package so the module works under any distribution name. Names
    # are forwarded lazily, keeping `import daytona.v1` as cheap as `import daytona`.
    _root = importlib.import_module(__name__.rpartition(".")[0])

__all__: list[str] = []
__all__.extend(_root.__all__)


def __getattr__(attr_name: str) -> object:
    if attr_name in __all__:
        value: object = getattr(_root, attr_name)
        globals()[attr_name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {attr_name!r}")


def __dir__() -> list[str]:
    return list(__all__)
