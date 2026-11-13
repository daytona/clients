#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
"""Sync the AX evals defined in evals.json + setup/*.sh to Firegoat.

Every eval is one task (evals.json "tasks") run through one surface ("surfaces"),
named <surface>-<task>. Creates a prompt for every eval without an id in
"promptIds" (and writes the new id back to evals.json), and updates the rest in
place. Prompts are kept active with no schedule, so they only run when the
ax_evals workflow requests them.

Environment:
  EVALS_API_KEY    Firegoat API key (required)
  DAYTONA_API_KEY  key the eval agent uses; required when creating a prompt,
                   optional on update (omitted = keep the stored value)

Usage: scripts/ax-evals/sync.py [--only python-preview-url,cli] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "evals.json"
MCP_URL = "https://firegoat.ai/evals/api/mcp"
TITLE_PREFIX = "[CI] clients v2"
DOCS = "Docs: https://www.daytona.com/docs"
CREDENTIALS = "Credentials are in the environment variable DAYTONA_API_KEY."

JsonObject = dict[str, Any]


class McpError(RuntimeError):
    pass


def _jsonrpc_response(raw: str) -> JsonObject:
    """Return the JSON-RPC response, skipping any notifications sent before it."""
    payloads = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")] or [raw]
    for payload in payloads:
        try:
            candidate = cast(JsonObject, json.loads(payload))
        except json.JSONDecodeError:
            continue
        if "result" in candidate or "error" in candidate:
            return candidate
    raise McpError(f"no JSON-RPC response in {raw[:200]!r}")


def call_tool(api_key: str, tool: str, arguments: JsonObject) -> JsonObject:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    ).encode()
    request = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = cast(bytes, response.read()).decode()
    # Streamable HTTP transport: the JSON-RPC response arrives as an SSE "data:" line.
    message = _jsonrpc_response(raw)
    if "error" in message:
        raise McpError(f"{tool}: {message['error']}")
    result = cast(JsonObject, message["result"])
    text = "".join(str(part.get("text", "")) for part in cast(list[JsonObject], result.get("content", [])))
    if result.get("isError"):
        raise McpError(f"{tool}: {text}")
    try:
        return cast(JsonObject, json.loads(text))
    except json.JSONDecodeError as exc:
        raise McpError(f"{tool}: unexpected response {text!r}") from exc


def eval_names(config: JsonObject) -> list[str]:
    return [f"{surface}-{task}" for surface in config["surfaces"] for task in config["tasks"]]


def split_name(config: JsonObject, name: str) -> tuple[str, str]:
    for surface in config["surfaces"]:
        if name.startswith(f"{surface}-") and name[len(surface) + 1 :] in config["tasks"]:
            return surface, name[len(surface) + 1 :]
    raise KeyError(name)


def prompt_fields(config: JsonObject, name: str) -> tuple[JsonObject, JsonObject]:
    """The prompt's text fields and its eval config, composed from evals.json."""
    surface_name, task_name = split_name(config, name)
    surface = cast(JsonObject, config["surfaces"][surface_name])
    task = cast(JsonObject, config["tasks"][task_name])
    defaults = cast(JsonObject, config["defaults"])
    fields: JsonObject = {
        "title": f"{TITLE_PREFIX} — {surface['name']}: {task['name']}",
        "text": f"{surface['lead']}, {task['text']}\n\n{surface['environment']} {DOCS}\n{CREDENTIALS}",
        "endGoal": task["endGoal"],
        "tags": defaults["tags"],
    }
    setup = (ROOT / "setup" / "_common.sh").read_text() + "\n" + (ROOT / "setup" / f"{surface_name}.sh").read_text()
    prompt_config: JsonObject = {
        "setupCommand": setup,
        "checks": {**task["checks"], "surface": surface["surfaceCheck"], **defaults["checks"]},
        "harnessModel": defaults["harnessModel"],
        "scaffold": "blank",
        "frequency": "none",
        "runsPerEval": 1,
    }
    return fields, prompt_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ = parser.add_argument("--only", help="comma-separated eval names or surfaces (default: all)")
    _ = parser.add_argument("--dry-run", action="store_true", help="print what would be sent")
    args = parser.parse_args()

    config = cast(JsonObject, json.loads(CONFIG.read_text()))
    names = eval_names(config)
    if args.only:
        tokens = [token.strip() for token in args.only.split(",") if token.strip()]
        unknown = sorted(t for t in tokens if t not in names and t not in config["surfaces"])
        if unknown:
            parser.error(f"unknown eval(s): {', '.join(unknown)}")
        names = [n for n in names if n in tokens or split_name(config, n)[0] in tokens]

    api_key = os.environ.get("EVALS_API_KEY", "")
    if not api_key and not args.dry_run:
        parser.error("EVALS_API_KEY is not set")
    daytona_key = os.environ.get("DAYTONA_API_KEY")
    env = {"DAYTONA_API_KEY": daytona_key} if daytona_key else None
    prompt_ids = cast(dict[str, str], config.setdefault("promptIds", {}))

    for name in names:
        fields, prompt_config = prompt_fields(config, name)
        if env:
            prompt_config["env"] = env
        prompt_id = prompt_ids.get(name)

        if args.dry_run:
            shown = {**prompt_config, "env": sorted(env) if env else "(unchanged)"}
            print(json.dumps({"eval": name, "promptId": prompt_id, **fields, **shown}, indent=2))
            continue

        if prompt_id is None:
            if env is None:
                print(f"{name}: DAYTONA_API_KEY is required to create a prompt", file=sys.stderr)
                return 1
            # Create archived so Firegoat does not fire an eval on creation, then activate.
            created = call_tool(api_key, "create_prompt", {**fields, **prompt_config, "status": "archived"})
            prompt_id = cast(str, created["promptId"])
            prompt_ids[name] = prompt_id
            _ = CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
            print(f"{name}: created {prompt_id}")
        else:
            _ = call_tool(api_key, "update_prompt_config", {"promptId": prompt_id, **prompt_config})
            print(f"{name}: updated {prompt_id}")
        _ = call_tool(
            api_key,
            "update_prompt",
            {"promptId": prompt_id, **fields, "status": "active", "allowPublishedTextEdit": True},
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
