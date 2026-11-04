# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Assignment targets use sequential stores after the complete RHS evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "case",
    [
        ('key, os.environ[key] = "DAYTONA_API_URL", url', True),
        ('[key, os.environ[key]] = ["DAYTONA_SERVER_URL", url]', True),
        ('(key, (other, os.environ[key])) = "DAYTONA_API_URL", (0, url)', True),
        ('env, env["DAYTONA_API_URL"] = os.environ, url', True),
        ('key = "DAYTONA_API_URL"\nkey, os.environ[key] = "OTHER", url', False),
        ('env = os.environ\nenv, env["DAYTONA_API_URL"] = {}, url', False),
        ('key = "DAYTONA_API_URL"\nkey, os.environ[key] = "OTHER", key', False),
        ('key = "DAYTONA_API_URL"\nkey, os.environ[key] = key, url', True),
        ('env, key = os.environ, "DAYTONA_API_URL"\nenv[key] = url', True),
        ("key, *rest, os.environ[key] = values", True),
        ('key = "OTHER"\nkey, os.environ[key] = values', True),
    ],
)
@pytest.mark.parametrize("local", [False, True])
def test_environment_write_when_targets_depend_on_earlier_stores(
    tmp_path: Path, case: tuple[str, bool], local: bool
) -> None:
    # Given dependent targets and a known custom URL, optionally in local scope.
    body, expected = case
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    prefix, indent = ("def configure():\n", "    ") if local else ("", "")
    (tmp_path / "app.py").write_text(
        "import os\n"
        + prefix
        + indent
        + "url = 'https://self/api'\n"
        + "\n".join(indent + line for line in body.splitlines())
        + "\n"
    )
    # When the original source is scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only actual possible endpoint writes are reported.
    assert any(item["kind"] == "env-api-url" for item in result.items) is expected


def test_custom_value_when_rhs_name_is_rebound_by_an_earlier_target(
    tmp_path: Path,
) -> None:
    # Given an RHS value that must be captured before either store executes.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "app.py").write_text(
        "import os\nurl = 'https://self/api'\n"
        "url, os.environ['DAYTONA_API_URL'] = 'https://app.daytona.io/api', url\n"
    )
    # When the assignment is scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the old custom value, not the new Cloud binding, triggers refusal.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize(
    "body",
    [
        "os.environ['DAYTONA_API_URL'], url = url, (url := 'https://app.daytona.io/api')",
        "first = os.environ['DAYTONA_API_URL'] = url",
        "key, os.environ[key] = 'DAYTONA_API_URL', url",
    ],
)
def test_custom_value_when_rhs_evaluation_precedes_all_target_stores(
    tmp_path: Path, body: str
) -> None:
    # Given a custom value and targets whose evaluation depends on assignment order.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "app.py").write_text("import os\nurl = 'https://self/api'\n" + body)
    # When scanning the complete assignment.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then a proven custom RHS value still refuses migration.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize(
    "source",
    [
        'RUN echo "DAYTONA_API_URL=https://self/api"\n',
        "RUN <<EOF\nDAYTONA_API_URL=https://self/api\nEOF\n",
        "COPY <<'EOF' /example\nENV DAYTONA_API_URL=https://self/api\nEOF\n",
        'ENV A="DAYTONA_API_URL=https://self/api"\n',
        'ARG A="DAYTONA_SERVER_URL=https://self/api"\n',
        'LABEL example="DAYTONA_API_URL=https://self/api"\n',
    ],
)
def test_docker_ignores_text_when_not_an_endpoint_declaration(
    tmp_path: Path, source: str
) -> None:
    # Given documentation or unrelated Docker instruction values.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(source)
    # When scanning actual Docker declarations.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then embedded variable names alone do not refuse the project.
    assert result.items == ()


@pytest.mark.parametrize(
    "source",
    [
        "RUN <<EOF\nENV DAYTONA_API_URL=https://ignored/api\nEOF\n"
        "ENV DAYTONA_API_URL=https://self/api\n",
        'ENV A="DAYTONA_API_URL=https://ignored/api" DAYTONA_API_URL=https://self/api\n',
        'ARG DAYTONA_API_URL="https://self/api"\n',
        'ENV "DAYTONA_API_URL=https://self/api"\n',
        "RUN <<END-OF-TEXT\nENV DAYTONA_API_URL=https://ignored/api\nEND-OF-TEXT\n"
        "ENV DAYTONA_API_URL=https://self/api\n",
        'RUN echo "<<EOF"\nENV DAYTONA_API_URL=https://self/api\n',
        "RUN cat <<<EOF\nENV DAYTONA_API_URL=https://self/api\n",
        "RUN <<-EOF\n\tDAYTONA_API_URL=https://ignored/api\n\tEOF\n"
        "ENV DAYTONA_API_URL=https://self/api\n",
    ],
)
def test_docker_refuses_when_real_endpoint_follows_irrelevant_text(
    tmp_path: Path, source: str
) -> None:
    # Given a real custom endpoint outside any heredoc or unrelated value.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(source)
    # When the scanner reads the Dockerfile.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then exactly the actual endpoint produces the paired diagnostics.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


def test_docker_keeps_manual_review_when_content_is_a_bare_assignment(
    tmp_path: Path,
) -> None:
    # Given legacy .env-shaped content stored under a Dockerfile name.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile.dev").write_text("DAYTONA_API_URL=https://self/api\n")
    # When the scanner cannot certify a Docker runtime declaration.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then it retains manual review without inventing literal refusal evidence.
    assert [item["kind"] for item in result.items] == ["env-api-url"]
