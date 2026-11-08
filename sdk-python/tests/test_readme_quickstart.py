# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execute the shipped quickstart against HTTP, not mocked SDK methods."""

import json
import re
import runpy
from pathlib import Path

import httpx
import pytest
import respx
from test_core import _log_frame_payload, _log_page_payload, _process_payload, _sandbox_detail_payload


def test_readme_quickstart_waits_for_import_and_deletion(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Given an image import which needs two long polls before it can be used.
    readme = Path(__file__).resolve().parents[1] / "README.md"
    source = re.findall(r"```python\n(.*?)```", readme.read_text(), re.DOTALL)[0]
    script = tmp_path / "quickstart.py"
    script.write_text(source)
    with respx.mock as wire:
        pull = wire.post("http://api.invalid/images").respond(
            202,
            json={"id": "img-pull", "status": "pending"},
        )
        image = wire.get("http://api.invalid/images/img-pull")
        image.side_effect = [
            httpx.Response(
                200,
                json={
                    "id": "img-pull",
                    "name": "py312",
                    "status": status,
                    "anonymous": False,
                    "region_id": "us",
                    "origin": "registry",
                    "region_ids": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "class": "linux",
                    "retention": "permanent",
                    "format_version": 1,
                    "format_outdated": False,
                },
            )
            for status in ("pending", "building", "ready")
        ]

        def create_ready(request: httpx.Request) -> httpx.Response:
            assert image.call_count == 3
            assert json.loads(request.content)["image"] == "py312"
            return httpx.Response(
                200,
                json=_sandbox_detail_payload(
                    "sbx-quickstart",
                    "running",
                    toolbox_proxy_url="http://toolbox.invalid",
                ),
            )

        create = wire.post("http://api.invalid/sandboxes").mock(side_effect=create_ready)
        wire.post("http://api.invalid/sandboxes/sbx-quickstart/access-token").respond(
            200,
            json={
                "access_token": "toolbox-jwt",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
        )
        command = wire.post("http://toolbox.invalid/sbx-quickstart/processes").respond(
            200,
            json={
                **_process_payload("proc-quickstart", "exec", "terminal"),
                "reason": "exited",
                "exitCode": 0,
                "logs": _log_page_payload(
                    [_log_frame_payload("c1", "Python 3.12.9\n")],
                    next_cursor="c1",
                    eof=True,
                ),
            },
        )
        delete = wire.delete("http://api.invalid/sandboxes/sbx-quickstart").respond(204)
        destroyed = wire.get("http://api.invalid/sandboxes/sbx-quickstart").respond(
            404,
            json={"code": "NOT_FOUND", "message": "deleted"},
        )

        # When running the exact README block, including its root context manager.
        runpy.run_path(str(script), run_name="__main__")

        # Then the named registry import precedes create, execution and completed deletion.
        assert json.loads(pull.calls[0].request.content)["name"] == "py312"
        assert json.loads(pull.calls[0].request.content)["source"] == {
            "type": "registry",
            "ref": "python:3.12",
        }
        assert [call.request.url.params.get("wait") for call in image.calls] == [None, "ready", "ready"]
        assert create.call_count == 1
        assert json.loads(command.calls[0].request.content)["shellCommand"] == "python -V"
        assert capsys.readouterr().out == "0 Python 3.12.9\n\n"
        assert delete.call_count == destroyed.call_count == 1
