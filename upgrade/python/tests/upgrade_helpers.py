# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Source analysis helpers for pytest-collected consumer modules."""

from __future__ import annotations

from textwrap import dedent

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate_test_module(source: str) -> SourceResult:
    """Analyze fixture consumers using a filename pytest collects."""
    source = dedent(source).lstrip()
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext(
            "test_app.py", "test_app", build_index({"test_app": source}, mapping)
        ),
    )
