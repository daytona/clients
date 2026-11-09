# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Offline tests for the Python examples.

Each example's ``main()`` runs against a fake v2 client built with
``create_autospec`` from the SDK's real synchronous classes, so a call the SDK
does not accept (an unknown keyword, a missing method) fails here the way it
would against a live deployment. Commands run in a fake sandbox answer from a
small responder; a test overrides individual commands to drive the failure
paths. Run from ``examples``::

    ../sdk-python/.venv/bin/python -m pytest -q python/test_examples.py
"""

from __future__ import annotations

import importlib
import inspect
import itertools
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, create_autospec

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(EXAMPLES / "python"), str(EXAMPLES)]

from _common import IMAGE, IMAGE_NAME, RUN, image_name

from daytona.v2 import (
    ImageBuilder,
    ImageBuildFailedError,
    InterpreterError,
    InterpreterLogs,
    InterpreterProcess,
    InterpreterResult,
    ListResult,
    NameConflictError,
    ProcessResult,
)
from daytona.v2._sync.daytona import Daytona as SyncDaytona
from daytona.v2._sync.filesystem import FileSystemService
from daytona.v2._sync.git import GitService
from daytona.v2._sync.interpreter import InterpreterService
from daytona.v2._sync.process import ProcessService
from daytona.v2._sync.sandbox import Sandbox
from daytona.v2._sync.services import ImageService, SecretService, VolumeService

# (pattern, exit_code, output): the first pattern that matches a command wins.
Override = tuple[str, int, str]

_CREATE_PARAMS = inspect.signature(SyncDaytona.create).parameters


class FakeWorld:
    """A fake Daytona project: sandboxes, secrets and volumes."""

    def __init__(self, overrides: tuple[Override, ...] = ()) -> None:
        self.overrides = overrides
        self.secrets: dict[str, str] = {}
        self.deleted_secrets: list[str] = []
        self.sandboxes: list[MagicMock] = []
        self.deleted_sandboxes: list[str] = []
        self.deleted_volumes: list[str] = []
        self.images: list[SimpleNamespace] = []
        self.image_ids = itertools.count()
        self.pull_conflict = False
        self.pulled_images: list[tuple[str, Optional[str]]] = []
        self.deleted_images: list[str] = []
        self.fail_builds_containing: Optional[str] = None
        self.empty_build_logs = False
        self.build_logs_fail_after_output = False
        self.interpreter_exit_code = 0
        self.interpreter_error: Optional[InterpreterError] = None
        self.client = self._client()

    # -- client -----------------------------------------------------------
    def _client(self) -> MagicMock:
        client: MagicMock = create_autospec(SyncDaytona, instance=True)
        client.create.side_effect = self._create
        client.create_from_build.side_effect = self._create_from_build
        client.delete.side_effect = lambda sandbox, **_: self._delete(sandbox)
        client.get.side_effect = lambda sandbox_id, **_: next(
            s for s in self.sandboxes if s.id == sandbox_id
        )
        client.iter.side_effect = lambda **_: iter(list(self.sandboxes))

        secret = create_autospec(SecretService, instance=True)
        secret.set.side_effect = self._secret_set
        secret.delete.side_effect = self._secret_delete
        secret.list.side_effect = lambda **_: ListResult(
            [SimpleNamespace(name=n) for n in self.secrets], None
        )
        secret.iter.side_effect = lambda **_: iter(
            [SimpleNamespace(name=n) for n in self.secrets]
        )
        client.secret = secret

        volume = create_autospec(VolumeService, instance=True)
        volume.create.side_effect = lambda *, name, size_gib=None: SimpleNamespace(
            id=f"vol-{name}", name=name, status="ready"
        )
        volume.delete.side_effect = lambda volume_id, **_: self.deleted_volumes.append(volume_id)
        client.volume = volume

        images = create_autospec(ImageService, instance=True)
        images.follow_build_logs.side_effect = self._follow_build_logs
        images.iter.side_effect = lambda *, name=None, **_: iter(
            [i for i in list(self.images) if name is None or i.name == name]
        )
        images.get.side_effect = self._image_ready
        images.pull.side_effect = self._image_pull
        images.delete.side_effect = self._image_delete
        client.images = images
        return client

    def add_image(self, name: str, status: str, waits: tuple[str, ...] = ("ready",)) -> SimpleNamespace:
        image = SimpleNamespace(id=f"img-{next(self.image_ids)}", name=name, status=status, waits=list(waits))
        self.images.append(image)
        return image

    def _image_ready(self, image_id: str, **_: object) -> SimpleNamespace:
        image = next(i for i in self.images if i.id == image_id)
        if image.waits:
            image.status = image.waits.pop(0)
        return image

    def _image_pull(self, image: str, *, name: Optional[str] = None, **_: object) -> SimpleNamespace:
        self.pulled_images.append((image, name))
        if self.pull_conflict and len(self.pulled_images) == 1:
            self.add_image(name or image, "ready")  # Another run registered it first.
            raise NameConflictError("image name already exists", status_code=409, code="NAME_CONFLICT")
        return self.add_image(name or image, "ready")

    def _image_delete(self, image_id: str) -> None:
        self.deleted_images.append(image_id)
        self.images = [i for i in self.images if i.id != image_id]

    def _create(self, image: Optional[str] = None, /, **kwargs: object) -> MagicMock:
        if image is not None and not isinstance(image, str):
            raise TypeError("create() accepts an image name or img_… id string")
        # The API creates only from a project image, never a registry reference.
        if not any(image in (i.name, i.id) and i.status == "ready" for i in self.images):
            raise LookupError(f"image not found: {image}")
        env = kwargs.get("env") or {}
        assert isinstance(env, Mapping)
        secrets = kwargs.get("secrets") or []
        assert isinstance(secrets, list)
        for name in secrets:
            # The API rejects a create that attaches a secret the project lacks.
            if name not in self.secrets:
                raise LookupError(f"secret {name} not found")
        sandbox_env = {**env, **{name: "DAYTONA_SECRET_PLACEHOLDER" for name in secrets}}
        return self._sandbox(str(kwargs.get("name")), sandbox_env, dockerfile="")

    def _create_from_build(self, builder: ImageBuilder, /, **kwargs: object) -> MagicMock:
        # create_from_build forwards these keywords to create(): hold them to
        # create()'s signature, as the SDK does at run time.
        for key in kwargs:
            if key not in _CREATE_PARAMS or key == "image":
                raise TypeError(f"create() got an unexpected keyword argument {key!r}")
        dockerfile = builder.render_dockerfile()
        if self.fail_builds_containing and self.fail_builds_containing in dockerfile:
            raise ImageBuildFailedError(
                "image build failed", status_code=409, code="IMAGE_BUILD_FAILED", image_id="img_failed"
            )
        return self._sandbox(str(kwargs.get("name")), {}, dockerfile=dockerfile)

    def _delete(self, sandbox: MagicMock) -> None:
        self.deleted_sandboxes.append(sandbox.id)

    def _secret_set(self, name: str, value: str, **kwargs: object) -> SimpleNamespace:
        if kwargs.get("if_not_exists") and name in self.secrets:
            raise AssertionError("secret already exists")
        self.secrets[name] = value
        return SimpleNamespace(name=name)

    def _secret_delete(self, name: str) -> None:
        self.secrets.pop(name, None)
        self.deleted_secrets.append(name)

    def _follow_build_logs(self, image_id: str, *, on_logs: Callable[[str], object], **_: object) -> None:
        if not self.empty_build_logs:
            on_logs(f"intentional-build-failure-{RUN}\n")
        if self.build_logs_fail_after_output:
            raise ConnectionError("stream interrupted")

    # -- sandbox ----------------------------------------------------------
    def _sandbox(self, name: str, env: Mapping[str, str], *, dockerfile: str) -> MagicMock:
        sandbox: MagicMock = create_autospec(Sandbox, instance=True)
        sandbox.id = f"sbx-{len(self.sandboxes)}"
        sandbox.name = name
        sandbox.state = "started"
        sandbox.cpu = 1
        sandbox.memory = 1
        files: dict[str, bytes] = {}

        process = create_autospec(ProcessService, instance=True)
        process.run.side_effect = lambda command=None, **_: self._run(str(command), env, dockerfile)
        sandbox.process = process

        interpreter = create_autospec(InterpreterService, instance=True)
        interpreter.run.side_effect = lambda code, **_: self._interpret(code)
        sandbox.interpreter = interpreter

        fs = create_autospec(FileSystemService, instance=True)
        fs.write_bytes.side_effect = lambda path, data, **_: files.__setitem__(path, data)
        fs.read_bytes.side_effect = lambda path, **_: files[path]

        def replace(paths: list[str], pattern: str, new_value: str, **_: object) -> None:
            for path in paths:
                files[path] = files[path].decode().replace(pattern, new_value).encode()

        fs.replace.side_effect = replace
        fs.list.return_value = SimpleNamespace(items=[])
        fs.glob.return_value = SimpleNamespace(items=[])
        sandbox.fs = fs

        git = create_autospec(GitService, instance=True)
        git.commit.return_value = SimpleNamespace(hash="abc1234")
        git.status.return_value = SimpleNamespace(current_branch="master")
        git.list_branches.return_value = SimpleNamespace(items=["master"])
        sandbox.git = git

        sandbox.build_logs.return_value = 'STEP 2: the marker\n'
        sandbox.delete.side_effect = lambda **_: self._delete(sandbox)
        self.sandboxes.append(sandbox)
        return sandbox

    def _run(self, command: str, env: Mapping[str, str], dockerfile: str) -> ProcessResult:
        for pattern, exit_code, output in self.overrides:
            if re.search(pattern, command):
                return _result(exit_code, output)
        if match := re.fullmatch(r'printf %s "\$(\w+)"', command):
            return _result(0, env.get(match.group(1), ""))
        if command == "cat /marker":
            marker = re.search(r"RUN echo (\S+) > /marker", dockerfile)
            return _result(0 if marker else 1, marker.group(1) if marker else "")
        if re.search(r"git -C \S+ log", command):
            return _result(0, "abc1234 add daytona-note")
        return _result(0, "")

    def _interpret(self, code: str) -> InterpreterResult:
        return InterpreterResult(
            process=InterpreterProcess(
                process_id="p", exit_code=self.interpreter_exit_code, signal=None, reason=None
            ),
            logs=InterpreterLogs(stdout="Hello World from code!\n"),
            error=self.interpreter_error,
        )


def _result(exit_code: int, output: str) -> ProcessResult:
    return ProcessResult(
        process_id="p", exit_code=exit_code, signal=None, reason=None, stdout=output, output=output
    )


@pytest.fixture
def run_example(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., tuple[int, FakeWorld]]]:
    def run(name: str, *overrides: Override, world: Optional[FakeWorld] = None) -> tuple[int, FakeWorld]:
        world = world or FakeWorld(overrides)
        module: ModuleType = importlib.import_module(name)
        monkeypatch.setattr(module, "client", lambda: world.client)
        return module.main(), world

    yield run


# -- every example runs against the SDK surface -----------------------------

DOCKERFILE_SANDBOX_OK: tuple[Override, ...] = (
    (r"^/app/greet\.sh$", 0, f"hello-from-the-image\nbuilt-for: {RUN}"),
    (r"^printenv GREETING$", 0, "hello-from-the-image"),
    (r"^pwd && cat build\.json$", 0, f'/app\n{{"build":"{RUN}","ok":true}}'),
)


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("exec_command", ()),
        ("file_operations", ()),
        ("lifecycle", ()),
        ("git_ops", ()),
        ("secrets_env", ()),
        ("dynamic_build", ()),
        ("dockerfile_sandbox", DOCKERFILE_SANDBOX_OK),
    ],
)
def test_example_succeeds_against_the_sdk_surface(
    run_example: Callable[..., tuple[int, FakeWorld]], name: str, overrides: tuple[Override, ...]
) -> None:
    code, world = run_example(name, *overrides)
    assert code == 0
    assert sorted(world.deleted_sandboxes) == sorted(s.id for s in world.sandboxes)


def test_registers_the_image_once_when_the_project_lacks_it(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    code, world = run_example("exec_command")
    assert code == 0
    assert world.pulled_images == [(IMAGE, IMAGE_NAME)]


def test_names_distinct_image_references_apart() -> None:
    assert re.fullmatch(r"examples-[a-z0-9-]+-[0-9a-f]{8}", IMAGE_NAME)
    names = [image_name(ref) for ref in ("foo:bar", "foo/bar", "foo.bar")]
    assert len(set(names)) == 3, names


def test_fails_rather_than_hangs_on_a_registration_that_never_finishes(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, "pending", ())
    with pytest.raises(RuntimeError, match=f"image {IMAGE_NAME} is still pending"):
        run_example("exec_command", world=world)
    assert world.sandboxes == []


@pytest.mark.parametrize(
    ("status", "waits"), [("ready", ()), ("pending", ("ready",)), ("pending", ("building", "ready"))]
)
def test_reuses_a_registered_image(
    run_example: Callable[..., tuple[int, FakeWorld]], status: str, waits: tuple[str, ...]
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, status, waits)
    code, world = run_example("exec_command", world=world)
    assert code == 0
    assert world.pulled_images == []


@pytest.mark.parametrize(("status", "waits"), [("failed", ()), ("pending", ("failed",))])
def test_registers_again_over_a_failed_registration(
    run_example: Callable[..., tuple[int, FakeWorld]], status: str, waits: tuple[str, ...]
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, status, waits)
    code, world = run_example("exec_command", world=world)
    assert code == 0
    assert world.deleted_images == ["img-0"]
    assert len(world.pulled_images) == 1


def test_uses_the_image_another_run_registered_first(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.pull_conflict = True
    code, world = run_example("exec_command", world=world)
    assert code == 0
    assert len(world.pulled_images) == 1


def test_build_logs_reads_the_failed_build_through_the_error_image_id(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.fail_builds_containing = "intentional-build-failure-"
    code, world = run_example("build_logs", world=world)
    assert code == 0
    world.client.images.follow_build_logs.assert_called_once()
    assert world.client.images.follow_build_logs.call_args.args == ("img_failed",)


# -- a failed check exits non-zero ------------------------------------------


# Each case names the FAIL line the injected failure must produce, so an
# unrelated error cannot pass the test.
@pytest.mark.parametrize(
    ("name", "override", "reported"),
    [
        ("exec_command", (r"Hello World from CMD", 1, ""), r"FAIL.* command failed \(exit=1\)"),
        ("lifecycle", (r"Hello World from run", 2, ""), r"FAIL.* run failed \(exit=2\)"),
        (
            "file_operations",
            (r"script\.sh$", 127, "not found"),
            r"FAIL.* the uploaded script failed \(exit=127\)",
        ),
        ("git_ops", (r"command -v git", 100, "apt failed"), r"FAIL.* could not ensure git: apt failed"),
    ],
)
def test_failed_command_fails_the_example(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
    name: str,
    override: Override,
    reported: str,
) -> None:
    code, world = run_example(name, override)
    assert code == 1
    assert re.search(reported, capsys.readouterr().out)
    assert world.deleted_sandboxes == [s.id for s in world.sandboxes]


@pytest.mark.parametrize(
    ("exit_code", "error", "reported"),
    [
        (1, None, r"FAIL.* code failed \(exit=1, error=None\)"),
        (0, InterpreterError(name="NameError", value="x"), r"FAIL.* code failed \(exit=0, error=.*NameError"),
    ],
)
def test_failed_interpreter_run_fails_exec_command(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
    exit_code: int,
    error: Optional[InterpreterError],
    reported: str,
) -> None:
    world = FakeWorld()
    world.interpreter_exit_code = exit_code
    world.interpreter_error = error
    code, _ = run_example("exec_command", world=world)
    assert code == 1
    assert re.search(reported, capsys.readouterr().out)


def test_build_logs_does_not_pass_partial_output_after_a_read_error(
    run_example: Callable[..., tuple[int, FakeWorld]], capsys: pytest.CaptureFixture[str]
) -> None:
    world = FakeWorld()
    world.fail_builds_containing = "intentional-build-failure-"
    world.build_logs_fail_after_output = True
    code, _ = run_example("build_logs", world=world)
    out = capsys.readouterr().out
    assert code == 1
    assert "the failed build's log could not be read: stream interrupted" in out
    assert "PASS\x1b[0m the failed build's log contains" not in out


def test_build_logs_fails_when_the_failed_build_log_is_empty(
    run_example: Callable[..., tuple[int, FakeWorld]], capsys: pytest.CaptureFixture[str]
) -> None:
    world = FakeWorld()
    world.fail_builds_containing = "intentional-build-failure-"
    world.empty_build_logs = True
    code, _ = run_example("build_logs", world=world)
    assert code == 1
    assert "the failed build's log was empty" in capsys.readouterr().out


# -- secrets_env ------------------------------------------------------------


def test_secrets_env_uses_a_run_scoped_secret_it_never_overwrites(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    code, world = run_example("secrets_env")
    assert code == 0
    world.client.secret.set.assert_called_once()
    name = world.client.secret.set.call_args.args[0]
    assert name == f"EXAMPLE_API_KEY_{RUN.upper()}"
    assert world.client.secret.set.call_args.kwargs["if_not_exists"] is True
    assert world.deleted_secrets == [name]


@pytest.mark.parametrize(
    ("override", "reported"),
    [
        pytest.param(
            (r'"\$LOG_LEVEL"', 0, "info"),
            r'FAIL.* \$LOG_LEVEL = "info" \(expected "debug", exit=0\)',
            id="env-mismatch",
        ),
        pytest.param(
            (r'"\$LOG_LEVEL"', 1, "debug"),
            r'FAIL.* \$LOG_LEVEL = "debug" \(expected "debug", exit=1\)',
            id="env-read-failed",
        ),
        pytest.param(
            (r'"\$EXAMPLE_API_KEY_', 0, f"sk-secret-{RUN}"),
            r"FAIL.* leaked the real secret value",
            id="secret-leak",
        ),
        pytest.param(
            (r'"\$EXAMPLE_API_KEY_', 0, ""),
            r"FAIL.* is not set inside the sandbox",
            id="placeholder-missing",
        ),
        pytest.param(
            (r'"\$EXAMPLE_API_KEY_', 1, "x"),
            r"FAIL.* reading \$EXAMPLE_API_KEY_\w+ failed \(exit=1\)",
            id="secret-read-failed",
        ),
    ],
)
def test_secrets_env_failed_check_exits_non_zero(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
    override: Override,
    reported: str,
) -> None:
    code, world = run_example("secrets_env", override)
    assert code == 1
    assert re.search(reported, capsys.readouterr().out)
    assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]


def test_secrets_env_fails_when_it_cannot_delete_its_secret(
    run_example: Callable[..., tuple[int, FakeWorld]], capsys: pytest.CaptureFixture[str]
) -> None:
    world = FakeWorld()
    world.client.secret.delete.side_effect = RuntimeError("delete failed")
    code, _ = run_example("secrets_env", world=world)
    assert code == 1
    assert re.search(r"FAIL.* delete secret EXAMPLE_API_KEY_\w+ failed: delete failed", capsys.readouterr().out)


def test_secrets_env_deletes_its_secret_when_listing_fails(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.client.secret.iter.side_effect = RuntimeError("list failed")
    with pytest.raises(RuntimeError, match="list failed"):
        run_example("secrets_env", world=world)
    assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]


def test_secrets_env_fails_when_it_cannot_delete_its_sandbox(
    run_example: Callable[..., tuple[int, FakeWorld]], capsys: pytest.CaptureFixture[str]
) -> None:
    world = FakeWorld()
    world.client.delete.side_effect = RuntimeError("delete failed")
    code, _ = run_example("secrets_env", world=world)
    assert code == 1
    assert re.search(r"FAIL.* delete sandbox sbx-0 failed: delete failed", capsys.readouterr().out)
    assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]


def test_secrets_env_leaves_secrets_it_did_not_create(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.client.secret.set.side_effect = RuntimeError("already exists")
    with pytest.raises(RuntimeError, match="already exists"):
        run_example("secrets_env", world=world)
    world.client.secret.delete.assert_not_called()


# -- volumes_share ----------------------------------------------------------


def _patch_volumes_share(monkeypatch: pytest.MonkeyPatch, world: FakeWorld) -> ModuleType:
    import volumes_share

    monkeypatch.setattr(volumes_share, "Daytona", lambda config: SimpleNamespace(v2=world.client))
    monkeypatch.setattr(volumes_share, "time", SimpleNamespace(sleep=lambda seconds: None))
    # DAYTONA_KEEP in the caller's shell would skip the cleanup under test.
    monkeypatch.setattr(volumes_share, "KEEP", False)
    return volumes_share


def _volume_world(read_fails: bool = False) -> FakeWorld:
    """A world whose sandboxes share /data/message.txt."""
    contents: list[str] = []
    world = FakeWorld()
    original = world._run

    def run(command: str, env: Mapping[str, str], dockerfile: str) -> ProcessResult:
        if match := re.search(r"echo (\S+) >>? /data/message\.txt", command):
            contents.append(match.group(1))
            return _result(0, "")
        if read_fails and command.endswith("then cat /data/message.txt; fi"):
            return _result(1, "Input/output error")
        if "/data/message.txt" in command:
            return _result(0, "\n".join(contents))
        return original(command, env, dockerfile)

    world._run = run  # type: ignore[method-assign]
    return world


def test_volumes_share_round_trip_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _volume_world()
    assert _patch_volumes_share(monkeypatch, world).main() == 0
    assert len(world.deleted_volumes) == 1


def test_volumes_share_reports_a_failed_write_at_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeWorld(((r"> /data/message\.txt", 1, "read-only file system"),))
    volumes_share = _patch_volumes_share(monkeypatch, world)
    with pytest.raises(RuntimeError, match=r"/data/message\.txt.*exit=1.*read-only file system"):
        volumes_share.main()
    assert world.deleted_sandboxes == [s.id for s in world.sandboxes]
    assert len(world.deleted_volumes) == 1


def test_volumes_share_reports_a_failed_read_instead_of_a_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _volume_world(read_fails=True)
    volumes_share = _patch_volumes_share(monkeypatch, world)
    with pytest.raises(RuntimeError, match=r"failed \(exit=1\): Input/output error"):
        volumes_share.main()
    assert len(world.deleted_volumes) == 1
