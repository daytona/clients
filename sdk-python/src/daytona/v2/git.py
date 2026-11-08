# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
from typing import Dict, Final, Optional, Sequence

from ._core import (
    RequestSpec,
    TransportPayload,
    _expect_model_from_dict,
    above_timer_maximum,
    checked_seconds,
    expect_branch_list,
    expect_commit_response,
    expect_object,
    expect_status_response,
    list_shape,
)
from ._gen.toolbox import CommitInfo
from ._timestamps import parse_timestamp
from ._transport import AsyncTransport
from .errors import create_error
from .types import (
    GitCommit,
    GitCommitResponse,
    GitConfigScope,
    GitRemote,
    GitStatus,
    JsonObject,
    JsonValue,
    ListResult,
)

GIT_REMOTE_TIMEOUT_SECONDS: Final[float] = 30 * 60


async def _json_payload(transport: AsyncTransport, spec: RequestSpec) -> JsonValue:
    payload = await transport.send(spec)
    if isinstance(payload, bytes):
        raise create_error("expected JSON object", 400, "BAD_REQUEST", "SDK")
    return payload


class AsyncGitService:
    """Sandbox git namespace."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def clone(
        self,
        url: str,
        path: str,
        *,
        branch: Optional[str] = None,
        commit_id: Optional[str] = None,
        depth: Optional[int] = None,
        insecure_skip_tls: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Clone a repository, optionally shallow and with per-call TLS relaxation."""
        body: JsonObject = {"url": url, "path": path}
        for key, value in {
            "branch": branch,
            "commit_id": commit_id,
            "username": username,
            "password": password,
        }.items():
            if value is not None:
                body[key] = value
        if depth is not None:
            body["depth"] = _git_clone_depth(depth)
        if _git_insecure_skip_tls(insecure_skip_tls):
            body["insecureSkipTls"] = True
        await _send_remote(self._transport, RequestSpec("POST", "/git/clone", json_body=body), timeout)

    async def init(
        self,
        path: str,
        *,
        bare: bool = False,
        initial_branch: Optional[str] = None,
    ) -> None:
        body: JsonObject = {"path": path, "bare": bare}
        if initial_branch is not None:
            body["initial_branch"] = initial_branch
        await self._transport.send(RequestSpec("POST", "/git/init", json_body=body))

    async def status(self, path: str) -> GitStatus:
        """Return git status."""
        response = await _json_payload(
            self._transport, RequestSpec("GET", "/git/status", params={"path": path})
        )
        return expect_status_response(response)

    async def list_branches(self, path: str) -> ListResult[str]:
        """List branch names as {items, next_cursor}."""
        payload = expect_branch_list(
            await _json_payload(self._transport, RequestSpec("GET", "/git/branches", params={"path": path}))
        )
        return ListResult(list(payload.branches), None)

    async def list_remotes(self, path: str) -> ListResult[GitRemote]:
        """List the configured remotes; URLs come back with any credentials removed."""
        payload = expect_object(
            await _json_payload(self._transport, RequestSpec("GET", "/git/remotes", params={"path": path}))
        )
        raw = payload.get("remotes")
        if not isinstance(raw, list):
            raise create_error(
                "expected git remotes response: remotes is required", 400, "BAD_REQUEST", "SDK"
            )
        return ListResult([_git_remote(expect_object(remote)) for remote in raw], None)

    async def history(self, path: str) -> ListResult[GitCommit]:
        """List commits, newest first."""
        items, next_cursor = list_shape(
            await _json_payload(self._transport, RequestSpec("GET", "/git/history", params={"path": path})),
            description="git history response",
        )
        return ListResult([_git_commit(_expect_commit_info(item)) for item in items], next_cursor)

    async def create_branch(self, path: str, name: str) -> None:
        """Create a branch."""
        await self._transport.send(
            RequestSpec("POST", "/git/branches", json_body={"path": path, "name": name})
        )

    async def add_remote(self, path: str, name: str, url: str) -> None:
        await self._transport.send(
            RequestSpec("POST", "/git/remotes", json_body={"path": path, "name": name, "url": url})
        )

    async def delete_branch(self, path: str, name: str) -> None:
        """Delete a branch."""
        await self._transport.send(
            RequestSpec("DELETE", "/git/branches", json_body={"path": path, "name": name})
        )

    async def checkout_branch(self, path: str, branch: str) -> None:
        """Checkout a branch."""
        await self._transport.send(
            RequestSpec("POST", "/git/checkout", json_body={"path": path, "branch": branch})
        )

    async def add(self, path: str, files: Sequence[str]) -> None:
        """Stage files."""
        await self._transport.send(
            RequestSpec("POST", "/git/add", json_body={"path": path, "files": list(files)})
        )

    async def reset(
        self,
        path: str,
        *,
        mode: Optional[str] = None,
        target: Optional[str] = None,
        files: Optional[Sequence[str]] = None,
    ) -> None:
        body: JsonObject = {"path": path}
        if mode is not None:
            body["mode"] = mode
        if target is not None:
            body["target"] = target
        if files is not None:
            body["files"] = list(files)
        await self._transport.send(RequestSpec("POST", "/git/reset", json_body=body))

    async def restore(
        self,
        path: str,
        files: Sequence[str],
        *,
        staged: Optional[bool] = None,
        worktree: Optional[bool] = None,
        source: Optional[str] = None,
    ) -> None:
        body: JsonObject = {"path": path, "files": list(files)}
        if staged is not None:
            body["staged"] = staged
        if worktree is not None:
            body["worktree"] = worktree
        if source is not None:
            body["source"] = source
        await self._transport.send(RequestSpec("POST", "/git/restore", json_body=body))

    async def commit(
        self,
        path: str,
        message: str,
        *,
        author: str,
        email: str,
        allow_empty: bool = False,
    ) -> GitCommitResponse:
        """Commit staged changes, optionally allowing an empty commit."""
        author = author.strip()
        email = email.strip()
        if not author or not email:
            raise create_error("git.commit requires author and email", 400, "BAD_REQUEST", "SDK")
        body: JsonObject = {"path": path, "message": message, "author": author, "email": email}
        if allow_empty:
            body["allow_empty"] = True
        return expect_commit_response(
            await _json_payload(
                self._transport,
                RequestSpec(
                    "POST",
                    "/git/commit",
                    json_body=body,
                ),
            )
        )

    async def pull(
        self,
        path: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        branch: Optional[str] = None,
        remote: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Pull from the configured or named remote."""
        await self._with_credentials(
            "/git/pull",
            path,
            username,
            password,
            branch=branch,
            remote=remote,
            set_upstream=False,
            timeout=timeout,
        )

    async def push(
        self,
        path: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        branch: Optional[str] = None,
        remote: Optional[str] = None,
        set_upstream: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Push to the configured or named remote."""
        await self._with_credentials(
            "/git/push",
            path,
            username,
            password,
            branch=branch,
            remote=remote,
            set_upstream=set_upstream,
            timeout=timeout,
        )

    async def get_config(
        self,
        key: str,
        *,
        path: Optional[str] = None,
        scope: GitConfigScope = "global",
    ) -> Optional[str]:
        params: Dict[str, JsonValue] = {"key": key, "scope": _config_scope(scope)}
        if path is not None:
            params["path"] = path
        payload = expect_object(
            await _json_payload(self._transport, RequestSpec("GET", "/git/config", params=params))
        )
        value = payload.get("value")
        if value is not None and not isinstance(value, str):
            raise create_error(
                "expected git config response: value must be a string or null", 400, "BAD_REQUEST", "SDK"
            )
        return value

    async def set_config(
        self,
        key: str,
        value: str,
        *,
        path: Optional[str] = None,
        scope: GitConfigScope = "global",
    ) -> None:
        body: Dict[str, JsonValue] = {"key": key, "value": value, "scope": _config_scope(scope)}
        if path is not None:
            body["path"] = path
        await self._transport.send(RequestSpec("POST", "/git/config", json_body=body))

    async def _with_credentials(
        self,
        route: str,
        path: str,
        username: Optional[str],
        password: Optional[str],
        *,
        branch: Optional[str],
        remote: Optional[str],
        set_upstream: bool,
        timeout: Optional[float],
    ) -> None:
        body: Dict[str, JsonValue] = {"path": path}
        if username is not None:
            body["username"] = username
        if password is not None:
            body["password"] = password
        if branch is not None:
            body["branch"] = branch
        if remote is not None:
            body["remote"] = remote
        if set_upstream:
            body["setUpstream"] = True
        await _send_remote(self._transport, RequestSpec("POST", route, json_body=body), timeout)


def _config_scope(scope: object) -> str:
    if scope not in ("global", "local"):
        raise create_error("scope must be 'global' or 'local'", 400, "BAD_REQUEST", "SDK")
    return str(scope)


def _git_remote_timeout(timeout: Optional[float]) -> float:
    """The total deadline of a remote git operation in seconds. The toolbox
    takes no timeout on these routes, so the deadline is the SDK's own:
    ``0``, or a value above the timer maximum, is no limit, as for every 2.x
    timeout."""
    if timeout is None:
        return GIT_REMOTE_TIMEOUT_SECONDS
    seconds = checked_seconds(timeout)
    return 0 if seconds == 0 or above_timer_maximum(seconds) else float(seconds)


async def _send_remote(transport: AsyncTransport, spec: RequestSpec, timeout: Optional[float]) -> None:
    """Send a clone / pull / push bounded by one total deadline for the whole
    request (a per-call timeout replaces the client's request timeout), as the
    TypeScript SDK does."""
    await transport.send(dataclasses.replace(spec, timeout=_git_remote_timeout(timeout)))


def _git_clone_depth(depth: int) -> int:
    if not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0:
        raise create_error("depth must be a positive integer", 400, "BAD_REQUEST", "SDK")
    return depth


def _git_insecure_skip_tls(value: bool) -> bool:
    if not isinstance(value, bool):
        raise create_error("insecure_skip_tls must be a boolean", 400, "BAD_REQUEST", "SDK")
    return value


def _expect_commit_info(value: TransportPayload) -> CommitInfo:
    return _expect_model_from_dict(value, CommitInfo, "commit history entry", CommitInfo.from_dict)


def _git_commit(payload: CommitInfo) -> GitCommit:
    return GitCommit(
        hash=payload.hash,
        message=payload.message,
        author=payload.author,
        email=payload.email,
        timestamp=parse_timestamp(payload.timestamp, "commit history entry: timestamp"),
    )


def _git_remote(payload: JsonObject) -> GitRemote:
    values: Dict[str, str] = {}
    for field in ("name", "url"):
        value = payload.get(field)
        if not isinstance(value, str):
            problem = "is required" if value is None else "must be a string"
            raise create_error(f"expected git remote: {field} {problem}", 400, "BAD_REQUEST", "SDK")
        values[field] = value
    return GitRemote(name=values["name"], url=values["url"])
