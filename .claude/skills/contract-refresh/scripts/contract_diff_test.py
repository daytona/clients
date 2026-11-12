#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
"""Run: python3 .claude/skills/contract-refresh/scripts/contract_diff_test.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).with_name("contract_diff.py")
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def spec(paths: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any]:
    return {"paths": paths, "components": {"schemas": schemas}}


class ContractDiffTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q", "-b", "main")
        (self.repo / "contract").mkdir()
        self.write(
            "api",
            spec(
                {"/sandbox": {"get": {}}, "/gone": {"get": {}}},
                {"State": {"enum": ["started"]}, "Old": {}},
            ),
        )
        self.write("toolbox", spec({}, {}))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "refresh")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, env=ENV, check=True)

    def write(self, name: str, content: dict[str, Any]) -> None:
        (self.repo / "contract" / f"{name}-openapi.json").write_text(json.dumps(content))

    def run_diff(self, *args: str) -> str:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            env=ENV,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def test_committed_refresh_is_reported_with_changed_definitions(self) -> None:
        self.write(
            "api",
            spec(
                {"/sandbox": {"get": {"parameters": [{"name": "limit"}]}}, "/new": {"get": {}}},
                {"State": {"enum": ["started", "stopped"]}, "New": {}},
            ),
        )
        self.git("commit", "-q", "-am", "refresh the contract")
        out = self.run_diff("main")
        self.assertIn("api paths added: ['/new']", out)
        self.assertIn("api paths removed: ['/gone']", out)
        self.assertIn("api paths changed: ['/sandbox']", out)
        self.assertIn("api schemas added: ['New']", out)
        self.assertIn("api schemas removed: ['Old']", out)
        self.assertIn("api schemas changed: ['State']", out)
        self.assertIn("toolbox paths changed: []", out)

    def test_uncommitted_refresh_is_reported(self) -> None:
        self.write("toolbox", spec({"/files": {"get": {}}}, {}))
        self.assertIn("toolbox paths added: ['/files']", self.run_diff("main"))


if __name__ == "__main__":
    unittest.main()
