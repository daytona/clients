# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import overload

from typing_extensions import Self

from .errors import create_error


@dataclass(frozen=True, slots=True)
class ImageBuilder:
    """A declarative image source: build one with :meth:`from_registry` or :meth:`from_dockerfile`."""

    _registry_ref: str | None = None
    _dockerfile: str | None = None
    _steps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self._registry_ref is None) == (self._dockerfile is None):
            raise create_error(
                "an ImageBuilder starts from ImageBuilder.from_registry(...) or ImageBuilder.from_dockerfile(...)",
                400,
                "BAD_REQUEST",
                "SDK",
            )

    @classmethod
    def from_registry(cls, ref: str) -> Self:
        cleaned = ref.strip()
        if not cleaned:
            raise create_error(
                "ImageBuilder.from_registry: ref must be a non-empty registry image reference",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return cls(_registry_ref=cleaned)

    @classmethod
    def from_dockerfile(cls, content: str) -> Self:
        cleaned = content.strip()
        if not cleaned:
            raise create_error(
                "ImageBuilder.from_dockerfile: content must be a non-empty Dockerfile",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return cls(_dockerfile=cleaned)

    def pip_install(self, *packages: str) -> Self:
        if not packages:
            raise create_error(
                "ImageBuilder.pip_install requires at least one package",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        cleaned = tuple(package.strip() for package in packages)
        if any(not package for package in cleaned):
            raise create_error(
                "ImageBuilder.pip_install packages must be non-empty strings",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        # `python -m pip` (works where `pip` is not on PATH) and POSIX-quoted
        # packages — byte-identical to TypeScript's `pipInstall`.
        command = "python -m pip install " + " ".join(shlex.quote(package) for package in cleaned)
        return self.run_commands(command)

    def run_commands(self, *commands: str) -> Self:
        if not commands:
            raise create_error(
                "ImageBuilder.run_commands requires at least one command",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        cleaned = tuple(command.strip() for command in commands)
        if any(not command for command in cleaned):
            raise create_error(
                "ImageBuilder.run_commands commands must be non-empty strings",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return type(self)(
            _registry_ref=self._registry_ref,
            _dockerfile=self._dockerfile,
            _steps=self._steps + tuple(f"RUN {command}" for command in cleaned),
        )

    @overload
    def env(self, name: str, value: str, /) -> Self: ...

    @overload
    def env(self, values: Mapping[str, str], /) -> Self: ...

    def env(self, name_or_values: str | Mapping[str, str], value: str | None = None, /) -> Self:
        if isinstance(name_or_values, str):
            entries = (
                (
                    self._require_text(name_or_values, "ImageBuilder.env name"),
                    self._require_text(value, "ImageBuilder.env value"),
                ),
            )
        else:
            entries = tuple(
                (
                    self._require_text(key, "ImageBuilder.env key"),
                    self._require_text(entry_value, "ImageBuilder.env value"),
                )
                for key, entry_value in name_or_values.items()
            )
        return type(self)(
            _registry_ref=self._registry_ref,
            _dockerfile=self._dockerfile,
            # ensure_ascii=False: TS renders with JSON.stringify, which leaves non-ASCII
            # as is; keep Dockerfile output consistent across SDKs and preserve values.
            _steps=self._steps
            + tuple(
                f"ENV {key}={json.dumps(entry_value, ensure_ascii=False)}" for key, entry_value in entries
            ),
        )

    def workdir(self, path: str, /) -> Self:
        return type(self)(
            _registry_ref=self._registry_ref,
            _dockerfile=self._dockerfile,
            _steps=self._steps + (f"WORKDIR {self._require_text(path, 'ImageBuilder.workdir path')}",),
        )

    def _request_source(self) -> tuple[dict[str, str] | None, str | None]:
        """The ``POST /images`` source: a bare registry ref is an import, anything else a Dockerfile build."""
        if self._registry_ref is not None and not self._steps:
            return {"type": "registry", "ref": self._registry_ref}, None
        return None, self.to_dockerfile()

    def to_dockerfile(self) -> str:
        """Render the Dockerfile the build request carries.

        Byte-identical to TypeScript's ``ImageBuilder.toDockerfile()`` for the
        same builder: one line per layer, a single trailing newline (a POSIX
        text file), no trailing whitespace.
        """
        if self._dockerfile is not None:
            base = self._dockerfile.rstrip()
        elif self._registry_ref is not None:
            base = f"FROM {self._registry_ref}"
        else:
            raise AssertionError("image builder has no source")
        layers = [base, *self._steps]
        return "\n".join(layers).rstrip() + "\n"

    @staticmethod
    def _require_text(value: str | None, label: str) -> str:
        if value is None or value.strip() == "":
            raise create_error(
                f"{label} must be a non-empty string",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return value
