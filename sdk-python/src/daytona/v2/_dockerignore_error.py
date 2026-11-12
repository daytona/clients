# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""BuildKit pattern failures with cross-SDK JSON quoting."""

import json


class PatternError(Exception):
    """An invalid pattern, either refused up front or evaluated during a walk."""

    def __init__(self, pattern: str, reason: str) -> None:
        self.pattern = pattern
        self.reason = reason
        super().__init__(f"invalid pattern {json.dumps(pattern, ensure_ascii=False)}: {reason}")
