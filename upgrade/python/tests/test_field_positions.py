# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dataclass pseudo-fields and uncertain keyword routing cannot certify clients."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize(
    ("declarations", "arguments"),
    [
        (
            "@dataclass(kw_only=flag)\nclass Worker:\n    client: Daytona = Daytona()",
            "Other()",
        ),
        (
            "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona, kw_only=flag)",
            "Other()",
        ),
        (
            "@dataclass\nclass Worker:\n    label: str = field(default='', kw_only=flag)\n    client: Daytona = Daytona()",
            "Daytona(), Other()",
        ),
    ],
)
def test_field_stays_manual_when_keyword_only_option_is_unresolved(
    declarations: str,
    arguments: str,
) -> None:
    # Given an opaque option that may leave client reachable by position.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        + declarations
        + f"\nWorker({arguments}).client.create()\n"
    )
    # When the tool cannot determine the generated positional signature.
    result = migrate(source)
    # Then it must not choose the default instead of a possible replacement.
    assert f"Worker({arguments}).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "annotation", ["ClassVar", "typing.ClassVar", "'ClassVar'", "'typing.ClassVar'"]
)
def test_field_stays_manual_when_bare_class_variable_precedes_argument(
    annotation: str,
) -> None:
    # Given a valid class variable that is absent from the generated constructor.
    source = (
        "from dataclasses import dataclass\nfrom typing import ClassVar\nimport typing\n"
        "from daytona import Daytona\n@dataclass\nclass Worker:\n"
        f"    label: {annotation} = 1\n    client: Daytona = Daytona()\n"
        "Worker(Other()).client.create()\n"
    )
    # When locating the first actual field argument.
    result = migrate(source)
    # Then the class variable does not shift the client argument's index.
    assert "Worker(Other()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("sentinel", ["KW_ONLY", "dataclasses.KW_ONLY", "'KW_ONLY'"])
def test_field_moves_when_keyword_only_sentinel_separates_positional_fields(
    sentinel: str,
) -> None:
    # Given a keyword-only label between two constructor-positioned fields.
    source = (
        "import dataclasses\nfrom dataclasses import dataclass, field, KW_ONLY\n"
        "from daytona import Daytona\n@dataclass\nclass Worker:\n"
        f"    _: {sentinel}\n    label: str = ''\n"
        "    client: Daytona = field(default=unknown, kw_only=False)\n"
        "Worker(Daytona()).client.create()\n"
    )
    # When a field explicitly opts back into positional routing after the sentinel.
    result = migrate(source)
    # Then neither the sentinel nor the keyword-only field takes a position.
    assert "Worker(Daytona()).client.v0.create()" in result.source
    assert result.items == ()


@pytest.mark.parametrize(
    "annotation",
    [
        "InitVar",
        "InitVar[Daytona]",
        "dataclasses.InitVar",
        "dataclasses.InitVar[Daytona]",
        "'InitVar[Daytona]'",
    ],
)
def test_constructor_input_does_not_replace_initvar_class_default(
    annotation: str,
) -> None:
    # Given an InitVar input that is never stored on the instance.
    source = (
        "import dataclasses\nfrom dataclasses import dataclass, InitVar\n"
        "from daytona import Daytona\nclass Other:\n    def create(self): pass\n"
        f"@dataclass\nclass Worker:\n    client: {annotation} = Other()\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When reading the instance attribute (which falls back to the class default).
    result = migrate(source)
    # Then an SDK argument to InitVar does not prove the unrelated receiver.
    assert "Worker(client=Daytona()).client.create()" in result.source
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize(
    "annotation", ["InitVar", "InitVar[str]", "dataclasses.InitVar[str]"]
)
def test_field_stays_manual_when_initvar_precedes_replacement_argument(
    annotation: str,
) -> None:
    # Given an InitVar before an ordinary field in the constructor signature.
    source = (
        "import dataclasses\nfrom dataclasses import dataclass, InitVar\n"
        "from daytona import Daytona\n@dataclass\nclass Worker:\n"
        f"    label: {annotation} = ''\n    client: Daytona = Daytona()\n"
        "Worker('job', Other()).client.create()\n"
    )
    # When mapping positional arguments to stored fields.
    result = migrate(source)
    # Then the constructor-only input still occupies its positional slot.
    assert "Worker('job', Other()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
