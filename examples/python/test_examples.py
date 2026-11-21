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
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NamedTuple, Optional
from unittest.mock import MagicMock, create_autospec

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(EXAMPLES / "python"), str(EXAMPLES)]

from _common import IMAGE, IMAGE_NAME, IMAGE_WAIT_SECONDS, RUN, image_name

from daytona.v2 import (
    BadRequestError,
    BarChart,
    Chart,
    ChartBar,
    ChartSeries,
    CompositeChart,
    GitCommitResponse,
    GitStatus,
    ImageBuilder,
    ImageBuildFailedError,
    InterpreterError,
    InterpreterLogs,
    InterpreterOutput,
    InterpreterProcess,
    InterpreterResult,
    LineChart,
    FileSearchResult,
    ListResult,
    ConflictError,
    LogFrame,
    NameConflictError,
    NotFoundError,
    ProcessResult,
    RequestTimeoutError,
    VolumeMount,
    ReplaceResult,
)
from daytona.v2._sync.daytona import Daytona as SyncDaytona
from daytona.v2._sync.filesystem import FileSystemService
from daytona.v2._sync.git import GitService
from daytona.v2._sync.interpreter import InterpreterService
from daytona.v2._sync.process import ProcessService
from daytona.v2._sync.sandbox import Sandbox
from daytona.v2._sync.services import ImagesService, SecretsService, VolumesService

# (pattern, exit_code, output): the first pattern that matches a command wins.
Override = tuple[str, int, str]

_CREATE_FROM_BUILD_PARAMS = inspect.signature(SyncDaytona.create_from_build).parameters


class Mount(NamedTuple):
    """A volume mount as the fake sandbox resolves paths through it."""

    volume_id: str
    mount_path: str
    subpath: Optional[str]
    read_only: bool


def _absolute(path: str) -> str:
    """The SDK refuses a relative sandbox path; the fake does too."""
    if not path.startswith("/"):
        raise BadRequestError(f"path must be an absolute path (starting with '/'): {path}")
    return path


class FakeWorld:
    """A fake Daytona project: sandboxes, secrets and volumes."""

    def __init__(self, overrides: tuple[Override, ...] = ()) -> None:
        self.overrides = overrides
        self.secrets: dict[str, str] = {}
        self.deleted_secrets: list[str] = []
        self.sandboxes: list[MagicMock] = []
        self.deleted_sandboxes: list[str] = []
        self.deleted_volumes: list[str] = []
        self.created_volumes: list[str] = []
        # The state every create hands back (a create whose wait ended before
        # the sandbox started returns it in "creating").
        self.sandbox_state = "started"
        self.images: list[SimpleNamespace] = []
        self.image_ids = itertools.count()
        self.pull_conflict = False
        self.pull_fails: Optional[str] = None
        self.pulled_images: list[tuple[str, Optional[str]]] = []
        self.deleted_images: list[str] = []
        self.image_waits: list[tuple[object, object]] = []
        # Each sandbox's mounts, and each volume's files.
        self.mounts: list[tuple[Mount, ...]] = []
        self.volume_files: dict[str, dict[str, bytes]] = {}
        self.volumes: dict[str, SimpleNamespace] = {}
        self.fail_builds_containing: Optional[str] = None
        self.empty_build_logs = False
        self.build_logs_fail_after_output = False
        self.interpreter_exit_code = 0
        self.interpreter_error: Optional[InterpreterError] = None
        # A figure the code shows comes back with its chart (matplotlib is in
        # the sandbox); False: it comes back with none.
        self.interpreter_charts = True
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

        secret = create_autospec(SecretsService, instance=True)
        secret.set.side_effect = self._secret_set
        secret.delete.side_effect = self._secret_delete
        secret.list.side_effect = lambda **_: ListResult(
            items=[SimpleNamespace(name=n) for n in self.secrets]
        )
        secret.iter.side_effect = lambda **_: iter(
            [SimpleNamespace(name=n) for n in self.secrets]
        )
        client.secrets = secret

        volume = create_autospec(VolumesService, instance=True)
        volume.create.side_effect = self._volume_create
        volume.get.side_effect = self._volume_get
        volume.delete.side_effect = self._volume_delete
        client.volumes = volume

        images = create_autospec(ImagesService, instance=True)
        images.follow_build_logs.side_effect = self._follow_build_logs
        images.iter.side_effect = lambda *, name=None, **_: iter(
            [i for i in list(self.images) if name is None or i.name == name]
        )
        images.get.side_effect = self._image_ready
        images.pull.side_effect = self._image_pull
        images.delete.side_effect = self._image_delete
        client.images = images
        return client

    def add_image(
        self,
        name: str,
        status: str,
        waits: tuple[str, ...] = ("ready",),
        error: Optional[str] = None,
    ) -> SimpleNamespace:
        image = SimpleNamespace(
            id=f"img-{next(self.image_ids)}",
            name=name,
            status=status,
            waits=list(waits),
            error=error,
        )
        self.images.append(image)
        return image

    def _image_ready(self, image_id: str, *, wait: object = None, timeout: object = None, **_: object) -> SimpleNamespace:
        # Without wait="ready", the image as it is now. With it, as the SDK's
        # budgeted wait: it follows the image until it settles, or raises
        # RequestTimeoutError when it never does.
        self.image_waits.append((wait, timeout))
        image = next(i for i in self.images if i.id == image_id)
        if wait != "ready":
            return image
        while image.status not in ("ready", "failed", "error") and image.waits:
            image.status = image.waits.pop(0)
        if image.status not in ("ready", "failed", "error"):
            raise RequestTimeoutError("image did not settle", status_code=408, code="REQUEST_TIMEOUT", source="SDK")
        return image

    # -- volumes: what the project has, as the SDK sees it ----------------------
    def _volume_create(self, *, name: str, size_gib: Optional[int] = None) -> SimpleNamespace:
        volume = SimpleNamespace(id=f"vol-{name}", name=name, status="ready")
        self.volumes[volume.id] = volume
        self.created_volumes.append(volume.id)
        return volume

    def _volume_find(self, name_or_id: str, *, id_only: bool = False) -> Optional[SimpleNamespace]:
        found = self.volumes.get(name_or_id)
        if found is None and not id_only:
            found = next((v for v in self.volumes.values() if v.name == name_or_id), None)
        return found

    def _volume_get(
        self, name_or_id: str, *, create: bool = False, id_only: bool = False, **_: object
    ) -> SimpleNamespace:
        # As the SDK: an absent volume is NotFoundError unless create=True.
        found = self._volume_find(name_or_id, id_only=id_only)
        if found is not None:
            return found
        if create and not id_only:
            return self._volume_create(name=name_or_id)
        raise NotFoundError(f"volume {name_or_id} not found", status_code=404, code="NOT_FOUND", source="API")

    def _volume_delete(self, name_or_id: str, *, id_only: bool = False, **_: object) -> None:
        # Deleting a volume deletes its files too.
        found = self._volume_find(name_or_id, id_only=id_only)
        if found is None:
            raise NotFoundError(f"volume {name_or_id} not found", status_code=404, code="NOT_FOUND", source="API")
        # As the API: a volume a sandbox still mounts is a conflict.
        live = (mounts for box, mounts in zip(self.sandboxes, self.mounts) if box.id not in self.deleted_sandboxes)
        if any(mount.volume_id == found.id for mounts in live for mount in mounts):
            raise ConflictError(f"volume {found.id} is in use", status_code=409, code="CONFLICT", source="API")
        del self.volumes[found.id]
        self.volume_files.pop(found.id, None)
        self.deleted_volumes.append(found.id)

    def _image_pull(self, image: str, *, name: Optional[str] = None, **_: object) -> SimpleNamespace:
        self.pulled_images.append((image, name))
        if self.pull_conflict and len(self.pulled_images) == 1:
            self.add_image(name or image, "ready")  # Another run registered it first.
            raise NameConflictError(
                "image name already exists", status_code=409, code="NAME_CONFLICT"
            )
        if self.pull_fails:
            return self.add_image(
                name or image, "pending", ("failed",), self.pull_fails
            )
        return self.add_image(name or image, "ready")

    def _image_delete(self, image_id: str) -> None:
        self.deleted_images.append(image_id)
        self.images = [i for i in self.images if i.id != image_id]

    def _create(self, image: Optional[str] = None, /, **kwargs: object) -> MagicMock:
        if image is not None and not isinstance(image, str):
            raise TypeError("create() accepts an image name or img_… id string")
        # The API creates only from a project image, never a registry reference.
        if not any(
            image in (i.name, i.id) and i.status == "ready" for i in self.images
        ):
            raise LookupError(f"image not found: {image}")
        return self._sandbox(
            str(kwargs.get("name")), self._sandbox_env(kwargs), dockerfile="", mounts=self._mounts(kwargs)
        )

    def _mounts(self, kwargs: Mapping[str, object]) -> tuple[Mount, ...]:
        """The mounts a create asks for (mount path without a trailing slash);
        the API rejects a mount of a volume the project lacks."""
        requested = kwargs.get("volumes") or ()
        # Any sequence of mounts, as the SDK's Sequence[VolumeMount] takes.
        assert isinstance(requested, Sequence) and not isinstance(requested, str)
        mounts: list[Mount] = []
        for mount in requested:
            assert isinstance(mount, VolumeMount)
            if mount.volume_id not in self.volumes:
                raise LookupError(f"volume {mount.volume_id} not found")
            mounts.append(
                Mount(mount.volume_id, mount.mount_path.rstrip("/"), mount.subpath, mount.read_only is True)
            )
        return tuple(mounts)

    def file_store(
        self, path: str, mounts: tuple[Mount, ...], disk: dict[str, bytes]
    ) -> tuple[dict[str, bytes], str, bool]:
        """Where a path lives: under a mount, in that volume below its subpath
        (and read-only when the mount is); else on the sandbox's own disk."""
        mount = next((m for m in mounts if path == m.mount_path or path.startswith(m.mount_path + "/")), None)
        if mount is None:
            return disk, path, False
        below = "" if mount.subpath is None else "/" + mount.subpath.strip("/")
        store = self.volume_files.setdefault(mount.volume_id, {})
        return store, below + path[len(mount.mount_path) :], mount.read_only

    def _sandbox_env(self, kwargs: Mapping[str, object]) -> dict[str, str]:
        env = kwargs.get("env") or {}
        assert isinstance(env, Mapping)
        secrets = kwargs.get("secrets") or []
        assert isinstance(secrets, list)
        for name in secrets:
            # The API rejects a create that attaches a secret the project lacks.
            if name not in self.secrets:
                raise LookupError(f"secret {name} not found")
        return {**env, **{name: "DAYTONA_SECRET_PLACEHOLDER" for name in secrets}}

    def _create_from_build(self, builder: ImageBuilder, /, **kwargs: object) -> MagicMock:
        # Hold the keywords to create_from_build()'s own signature, as Python does.
        for key in kwargs:
            if key not in _CREATE_FROM_BUILD_PARAMS or key == "builder":
                raise TypeError(f"create_from_build() got an unexpected keyword argument {key!r}")
        dockerfile = builder.to_dockerfile()
        if self.fail_builds_containing and self.fail_builds_containing in dockerfile:
            raise ImageBuildFailedError(
                "image build failed",
                status_code=409,
                code="IMAGE_BUILD_FAILED",
                image_id="img_failed",
            )
        # Like create(): the env reaches the sandbox, attached secrets as placeholders.
        return self._sandbox(
            str(kwargs.get("name")), self._sandbox_env(kwargs), dockerfile=dockerfile, mounts=self._mounts(kwargs)
        )

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

    def _follow_build_logs(
        self, image_id: str, *, on_logs: Callable[[str], object], **_: object
    ) -> None:
        if not self.empty_build_logs:
            on_logs(f"intentional-build-failure-{RUN}\n")
        if self.build_logs_fail_after_output:
            raise ConnectionError("stream interrupted")

    # -- sandbox ----------------------------------------------------------
    def _sandbox(
        self, name: str, env: Mapping[str, str], *, dockerfile: str, mounts: tuple[Mount, ...] = ()
    ) -> MagicMock:
        sandbox: MagicMock = create_autospec(Sandbox, instance=True)
        self.mounts.append(mounts)
        # One store for the commands and the fs alike: a mounted path lives in
        # its volume, any other on this sandbox's own disk.
        disk: dict[str, bytes] = {}
        sandbox.id = f"sbx-{len(self.sandboxes)}"
        sandbox.name = name
        sandbox.state = self.sandbox_state
        sandbox.cpu = 1
        sandbox.memory = 1

        def write(path: str, data: bytes) -> None:
            store, key, read_only = self.file_store(path, mounts, disk)
            if read_only:
                raise OSError(f"sh: can't create {path}: Read-only file system")
            store[key] = data

        def read(path: str) -> bytes:
            held, at, _ = self.file_store(path, mounts, disk)
            return held[at]

        def list_dir(path: str, **_: object) -> ListResult[SimpleNamespace]:
            # The SDK refuses a relative path, as the real one does.
            relative = _absolute(path).rstrip("/")
            if relative == "":
                prefix = "/"
                held, _at, _read_only = self.file_store(prefix, mounts, disk)
            else:
                held, at, _read_only = self.file_store(relative, mounts, disk)
                prefix = at + "/"
            names = {
                p[len(prefix) :].split("/")[0]
                for p in held
                if p.startswith(prefix) and (prefix or not p.startswith("/"))
            }
            return ListResult(items=[SimpleNamespace(name=name) for name in sorted(names)])

        process = create_autospec(ProcessService, instance=True)
        process.run.side_effect = lambda command=None, **_: self._run(str(command), env, dockerfile, mounts, disk)
        sandbox.process = process

        interpreter = create_autospec(InterpreterService, instance=True)
        interpreter.run.side_effect = lambda code, **_: self._interpret(code)
        sandbox.interpreter = interpreter

        fs = create_autospec(FileSystemService, instance=True)
        fs.write_bytes.side_effect = lambda path, data, **_: write(_absolute(path), data)
        fs.read_bytes.side_effect = lambda path, **_: read(_absolute(path))

        def replace(
            paths: list[str], pattern: str, new_value: str, **_: object
        ) -> ListResult[ReplaceResult]:
            for path in paths:
                write(path, read(path).decode().replace(pattern, new_value).encode())
            return ListResult(
                items=[ReplaceResult(file=path, success=True) for path in paths]
            )

        fs.replace.side_effect = replace
        fs.list.side_effect = list_dir
        fs.glob.return_value = FileSearchResult(items=[])
        sandbox.fs = fs

        git = create_autospec(GitService, instance=True)
        git.commit.return_value = GitCommitResponse(hash="abc1234")
        git.status.return_value = GitStatus(
            current_branch="master",
            ahead=0,
            behind=0,
            branch_published=False,
            file_status=(),
        )
        git.list_branches.return_value = ListResult(items=["master"])
        sandbox.git = git

        sandbox.build_logs.return_value = "STEP 2: the marker\n"
        sandbox.delete.side_effect = lambda **_: self._delete(sandbox)
        self.sandboxes.append(sandbox)
        return sandbox

    def _run(
        self,
        command: str,
        env: Mapping[str, str],
        dockerfile: str,
        mounts: tuple[Mount, ...] = (),
        disk: Optional[dict[str, bytes]] = None,
    ) -> ProcessResult:
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
        results: tuple[InterpreterOutput, ...] = ()
        charts: tuple[Chart, ...] = ()
        if "plt.show()" in code:
            # The figure examples/python/charts.py draws.
            figure = CompositeChart(
                elements=(
                    LineChart(
                        x_scale="linear",
                        y_scale="linear",
                        elements=(ChartSeries(points=((1, 10), (2, 20), (3, 15), (4, 30)), label="visits"),),
                        title="traffic",
                    ),
                    BarChart(
                        orientation="vertical",
                        elements=tuple(ChartBar(label=k, value=v) for k, v in (("a", 3), ("b", 7), ("c", 5))),
                        title="signups",
                    ),
                )
            )
            chart = figure if self.interpreter_charts else None
            results = (InterpreterOutput(data={"image/png": "iVBOR"}, is_main_result=False, png="iVBOR", chart=chart),)
            charts = (figure,) if chart is not None else ()
        return InterpreterResult(
            process=InterpreterProcess(
                process_id="p", exit_code=self.interpreter_exit_code, reason="exited"
            ),
            results=results,
            logs=InterpreterLogs(stdout="Hello World from code!\n"),
            error=self.interpreter_error,
            charts=charts,
        )


def _result(exit_code: int, output: str) -> ProcessResult:
    # Non-empty output is one stdout log frame, as the sandbox reports it.
    logs = (
        (LogFrame(cursor="1", channel="stdout", data=output, timestamp_ms=0),)
        if output
        else ()
    )
    return ProcessResult(
        process_id="p",
        exit_code=exit_code,
        reason="exited",
        stdout=output,
        output=output,
        logs=logs,
    )


@pytest.fixture
def run_example(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., tuple[int, FakeWorld]]]:
    def run(
        name: str, *overrides: Override, world: Optional[FakeWorld] = None
    ) -> tuple[int, FakeWorld]:
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

LOCAL_FILES_OK: tuple[Override, ...] = (
    (r"^\./main\.sh && cat /etc/example/settings\.conf$", 0, f"local-files-{RUN}\nrun={RUN}"),
    (r"^ls -a /app$", 0, ". .. main.sh"),
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
        ("local_files", LOCAL_FILES_OK),
        ("charts", ()),
    ],
)
def test_example_succeeds_against_the_sdk_surface(
    run_example: Callable[..., tuple[int, FakeWorld]],
    name: str,
    overrides: tuple[Override, ...],
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


def test_the_fake_images_get_waits_only_when_asked() -> None:
    # As the SDK: get() without wait="ready" returns the image as it is now;
    # with it, the image once it settles (twin in examples.test.ts).
    world = FakeWorld()
    image = world.add_image("img", "pending", ("ready",))
    assert world.client.images.get(image.id).status == "pending"
    assert world.client.images.get(image.id, wait="ready", timeout=5).status == "ready"


def test_names_distinct_image_references_apart() -> None:
    assert re.fullmatch(r"examples-[a-z0-9-]+-[0-9a-f]{8}", IMAGE_NAME)
    names = [image_name(ref) for ref in ("foo:bar", "foo/bar", "foo.bar")]
    assert len(set(names)) == 3, names


def test_fails_rather_than_hangs_on_a_registration_that_never_finishes(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, "pending", ())
    with pytest.raises(RuntimeError, match=f"image {IMAGE_NAME} did not settle within {IMAGE_WAIT_SECONDS} s"):
        run_example("exec_command", world=world)
    assert world.sandboxes == []


@pytest.mark.parametrize(
    ("status", "waits"),
    [("ready", ()), ("pending", ("ready",)), ("pending", ("building", "ready"))],
)
def test_reuses_a_registered_image(
    run_example: Callable[..., tuple[int, FakeWorld]],
    status: str,
    waits: tuple[str, ...],
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, status, waits)
    code, world = run_example("exec_command", world=world)
    assert code == 0
    assert world.pulled_images == []
    # One budgeted wait for an image still registering, none for a ready one.
    assert world.image_waits == ([] if status == "ready" else [("ready", IMAGE_WAIT_SECONDS)])


@pytest.mark.parametrize(
    ("status", "waits"), [("failed", ()), ("pending", ("failed",))]
)
def test_registers_again_over_a_failed_registration(
    run_example: Callable[..., tuple[int, FakeWorld]],
    status: str,
    waits: tuple[str, ...],
) -> None:
    world = FakeWorld()
    world.add_image(IMAGE_NAME, status, waits)
    code, world = run_example("exec_command", world=world)
    assert code == 0
    assert world.deleted_images == ["img-0"]
    assert len(world.pulled_images) == 1


def test_reports_why_the_registration_failed_after_its_retries(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.pull_fails = "manifest unknown"
    with pytest.raises(RuntimeError, match=f"as image {IMAGE_NAME}: manifest unknown$"):
        run_example("exec_command", world=world)
    assert len(world.pulled_images) == 3
    assert world.sandboxes == []


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


def test_build_logs_fails_when_a_deliberately_broken_build_unexpectedly_succeeds(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The fake accepts every Dockerfile, the broken one included.
    code, world = run_example("build_logs")
    assert code == 1
    assert re.search(r"FAIL.* the deliberately broken build unexpectedly created", capsys.readouterr().out)
    assert sorted(world.deleted_sandboxes) == sorted(s.id for s in world.sandboxes)


def test_the_fake_lists_its_own_disk_and_refuses_relative_paths() -> None:
    # Twin in volume-lifecycle.ts (examples.test.ts).
    world = FakeWorld()
    box = world._create_from_build(ImageBuilder.from_registry("python:3.12"), name="b")
    with pytest.raises(BadRequestError, match="absolute"):
        box.fs.write_bytes("notes.txt", b"n")
    with pytest.raises(BadRequestError, match="absolute"):
        box.fs.list(".")
    box.fs.write_bytes("/root.txt", b"r")
    assert [entry.name for entry in box.fs.list("/").items] == ["root.txt"]


def test_the_fake_create_from_build_carries_env_and_secrets_like_create() -> None:
    world = FakeWorld()
    world.secrets["TOKEN"] = "real"
    box = world._create_from_build(ImageBuilder.from_registry("python:3.12"), name="b", env={"A": "1"}, secrets=["TOKEN"])
    assert box.process.run('printf %s "$A"').output == "1"
    assert box.process.run('printf %s "$TOKEN"').output == "DAYTONA_SECRET_PLACEHOLDER"
    with pytest.raises(LookupError):
        world._create_from_build(ImageBuilder.from_registry("python:3.12"), name="c", secrets=["MISSING"])


# -- a failed check exits non-zero ------------------------------------------


# Each case names the FAIL line the injected failure must produce, so an
# unrelated error cannot pass the test.
@pytest.mark.parametrize(
    ("name", "override", "reported"),
    [
        (
            "exec_command",
            (r"Hello World from CMD", 1, ""),
            r"FAIL.* command failed \(exit=1\)",
        ),
        (
            "lifecycle",
            (r"Hello World from run", 2, ""),
            r"FAIL.* run failed \(exit=2\)",
        ),
        (
            "file_operations",
            (r"script\.sh$", 127, "not found"),
            r"FAIL.* the uploaded script failed \(exit=127\)",
        ),
        (
            "git_ops",
            (r"command -v git", 100, "apt failed"),
            r"FAIL.* could not ensure git: apt failed",
        ),
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
        (
            0,
            InterpreterError(name="NameError", value="x"),
            r"FAIL.* code failed \(exit=0, error=.*NameError",
        ),
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


def test_charts_reports_each_subplots_data(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = run_example("charts")
    out = capsys.readouterr().out
    assert code == 0
    assert "line 'traffic' (visits: 4 points)" in out
    assert "bar 'signups' (a=3, b=7, c=5)" in out
    assert "image: 5 base64 characters of PNG" in out


def test_charts_fails_when_the_figure_exits_non_zero(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = FakeWorld()
    world.interpreter_exit_code = 1
    code, _ = run_example("charts", world=world)
    assert code == 1
    assert "the figure failed (exit=1, no error output)" in capsys.readouterr().out


def test_charts_fails_when_the_figure_comes_back_without_its_chart(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a sandbox whose interpreter returns the figure without chart data.
    world = FakeWorld()
    world.interpreter_charts = False
    # When the example runs.
    code, _ = run_example("charts", world=world)
    # Then it fails, says why, and still deletes its sandbox.
    assert code == 1
    assert "expected one figure with subplots, got []" in capsys.readouterr().out
    assert world.deleted_sandboxes == [s.id for s in world.sandboxes]


def test_charts_fails_when_matplotlib_cannot_be_installed(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, world = run_example("charts", (r"pip install", 1, "No matching distribution found for matplotlib"))
    assert code == 1
    assert "pip install failed (exit=1)" in capsys.readouterr().out
    assert world.deleted_sandboxes == [s.id for s in world.sandboxes]


def test_build_logs_does_not_pass_partial_output_after_a_read_error(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
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
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
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
    world.client.secrets.set.assert_called_once()
    name = world.client.secrets.set.call_args.args[0]
    assert name == f"EXAMPLE_API_KEY_{RUN.upper()}"
    assert world.client.secrets.set.call_args.kwargs["if_not_exists"] is True
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
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = FakeWorld()
    world.client.secrets.delete.side_effect = RuntimeError("delete failed")
    code, _ = run_example("secrets_env", world=world)
    assert code == 1
    assert re.search(
        r"FAIL.* delete secret EXAMPLE_API_KEY_\w+ failed: delete failed",
        capsys.readouterr().out,
    )


def test_secrets_env_deletes_its_secret_when_listing_fails(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.client.secrets.iter.side_effect = RuntimeError("list failed")
    with pytest.raises(RuntimeError, match="list failed"):
        run_example("secrets_env", world=world)
    assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]


def test_secrets_env_fails_when_it_cannot_delete_its_sandbox(
    run_example: Callable[..., tuple[int, FakeWorld]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = FakeWorld()
    world.client.delete.side_effect = RuntimeError("delete failed")
    code, _ = run_example("secrets_env", world=world)
    assert code == 1
    assert re.search(
        r"FAIL.* delete sandbox sbx-0 failed: delete failed", capsys.readouterr().out
    )
    assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]


def test_secrets_env_leaves_secrets_it_did_not_create(
    run_example: Callable[..., tuple[int, FakeWorld]],
) -> None:
    world = FakeWorld()
    world.client.secrets.set.side_effect = RuntimeError("already exists")
    with pytest.raises(RuntimeError, match="already exists"):
        run_example("secrets_env", world=world)
    world.client.secrets.delete.assert_not_called()


# -- volumes_share ----------------------------------------------------------


def _patch_volumes_share(
    monkeypatch: pytest.MonkeyPatch, world: FakeWorld
) -> ModuleType:
    import volumes_share

    monkeypatch.setattr(
        volumes_share, "Daytona", lambda config: SimpleNamespace(v2=world.client)
    )
    monkeypatch.setattr(
        volumes_share, "time", SimpleNamespace(sleep=lambda seconds: None)
    )
    # DAYTONA_KEEP in the caller's shell would skip the cleanup under test.
    monkeypatch.setattr(volumes_share, "KEEP", False)
    return volumes_share


def _volume_world(read_fails: bool = False) -> FakeWorld:
    """A world whose sandboxes read and write /data/message.txt through the
    volume mounted over /data, else on their own disk."""
    world = FakeWorld()
    original = world._run

    def run(
        command: str,
        env: Mapping[str, str],
        dockerfile: str,
        mounts: tuple[Mount, ...] = (),
        disk: Optional[dict[str, bytes]] = None,
    ) -> ProcessResult:
        path = "/data/message.txt"
        if path not in command:
            return original(command, env, dockerfile, mounts, disk)
        assert disk is not None
        store, key, read_only = world.file_store(path, mounts, disk)
        match = re.search(r"echo (\S+) (>>?) /data/message\.txt", command)
        if match and read_only:
            return _result(1, "sh: can't create /data/message.txt: Read-only file system")
        if match:
            kept = store.get(key, b"") if match.group(2) == ">>" else b""
            store[key] = kept + f"{match.group(1)}\n".encode()
            return _result(0, "")
        if read_fails and command.endswith("then cat /data/message.txt; fi"):
            return _result(1, "Input/output error")
        if key not in store:
            return _result(0, "<missing>" if "echo '<missing>'" in command else "")
        return _result(0, store[key].decode().removesuffix("\n"))

    world._run = run  # type: ignore[method-assign]
    return world


def test_volumes_share_round_trip_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake keeps /data on a sandbox's own disk unless a volume is mounted
    # there, so the round trip passes only through the mounted volume.
    world = _volume_world()
    assert _patch_volumes_share(monkeypatch, world).main() == 0
    assert len(world.deleted_volumes) == 1
    assert world.mounts == [(Mount(world.created_volumes[0], "/data", None, False),)] * len(world.sandboxes)


def test_the_fake_volumes_follow_the_sdk() -> None:
    # As the SDK: get() of an absent volume without create=True is
    # NotFoundError, and a deleted volume's files go with it, so a volume
    # created again under the same name starts empty (twin in examples.test.ts).
    world = _volume_world()
    with pytest.raises(NotFoundError):
        world.client.volumes.get("absent")
    assert world.client.volumes.get("absent", create=True).id == "vol-absent"

    def write_then_read(volume_id: str) -> str:
        mount = VolumeMount(volume_id=volume_id, mount_path="/data")
        box = world._create_from_build(ImageBuilder.from_registry("python:3.12"), name="b", volumes=[mount])
        before = box.process.run("if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi")
        # The write must land, through the command and the fs alike.
        assert box.process.run("echo written > /data/message.txt").exit_code == 0
        assert box.fs.read_bytes("/data/message.txt") == b"written\n"
        box.fs.write_bytes("/data/note.txt", b"note")
        assert [entry.name for entry in box.fs.list("/data").items] == ["message.txt", "note.txt"]
        output: str = before.output
        return output

    assert write_then_read("vol-absent") == "<missing>"
    # A mounted volume cannot be deleted; deleting its sandbox first frees it.
    with pytest.raises(ConflictError):
        world.client.volumes.delete("vol-absent", id_only=True)
    world.sandboxes[-1].delete()
    world.client.volumes.delete("vol-absent", id_only=True)
    with pytest.raises(NotFoundError):
        world.client.volumes.get("absent")
    assert write_then_read(world.client.volumes.create(name="absent").id) == "<missing>"


def test_the_fake_applies_a_mount_subpath_and_read_only() -> None:
    # The fake resolves a path below the mount's subpath, and refuses a write
    # through a read-only mount, as the sandbox does (twin in examples.test.ts).
    world = _volume_world()
    volume = world.client.volumes.create(name="mounts")

    def box(subpath: str, read_only: Optional[bool] = None) -> MagicMock:
        mount = VolumeMount(volume_id=volume.id, mount_path="/data", subpath=subpath, read_only=read_only)
        return world._create_from_build(ImageBuilder.from_registry("python:3.12"), name=subpath, volumes=(mount,))

    def run(sandbox: MagicMock, command: str) -> tuple[int, str]:
        result = sandbox.process.run(command)
        return result.exit_code, result.output

    assert run(box("a"), "echo from-a > /data/message.txt") == (0, "")
    missing = "if [ -e /data/message.txt ]; then cat /data/message.txt; else echo '<missing>'; fi"
    assert run(box("b"), missing) == (0, "<missing>")
    reader = box("a", read_only=True)
    assert run(reader, "cat /data/message.txt") == (0, "from-a")
    code, output = run(reader, "echo again > /data/message.txt")
    assert code == 1 and output.endswith("Read-only file system")


def test_volumes_share_reports_a_failed_write_at_its_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = FakeWorld(((r"> /data/message\.txt", 1, "read-only file system"),))
    volumes_share = _patch_volumes_share(monkeypatch, world)
    with pytest.raises(
        RuntimeError, match=r"/data/message\.txt.*exit=1.*read-only file system"
    ):
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


@pytest.mark.parametrize(
    "name",
    [
        "exec_command",
        "file_operations",
        "git_ops",
        "secrets_env",
        "dynamic_build",
        "dockerfile_sandbox",
        "local_files",
        "volumes",
        "lifecycle",
        "charts",
    ],
)
def test_an_example_skips_work_and_cleans_up_when_create_returns_a_non_started_sandbox(
    run_example: Callable[..., tuple[int, FakeWorld]], name: str
) -> None:
    # The TypeScript twin is the non-started loop in typescript/examples.test.ts.
    world = FakeWorld()
    world.sandbox_state = "creating"
    code, _ = run_example(name, world=world)
    assert code == 1
    assert len(world.sandboxes) == 1
    assert all(sandbox.process.run.call_count == 0 for sandbox in world.sandboxes)
    assert sorted(world.deleted_sandboxes) == sorted(sandbox.id for sandbox in world.sandboxes)
    # The other resources the example acquired are cleaned up too.
    if name == "secrets_env":
        assert world.deleted_secrets == [f"EXAMPLE_API_KEY_{RUN.upper()}"]
    if name == "volumes":
        assert len(world.created_volumes) == 1
        assert world.deleted_volumes == world.created_volumes


def test_the_test_double_reports_a_commands_output_as_a_stdout_log_frame() -> None:
    assert [(frame.channel, frame.data) for frame in _result(0, "out").logs] == [
        ("stdout", "out")
    ]
    assert _result(0, "").logs == ()
