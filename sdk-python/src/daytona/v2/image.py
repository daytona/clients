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


@dataclass(frozen=True, slots=True, kw_only=True)
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

    # One rule in both SDKs: every value must be a string that is not blank by
    # Python's whitespace set, and text rendered raw into a Dockerfile line
    # (the registry ref, the Dockerfile, commands, packages, env keys, the
    # workdir) is stripped with that set; an env value, which is rendered as a
    # JSON string, is kept as given. TypeScript applies the same rule.

    @classmethod
    def from_registry(cls, ref: str) -> Self:
        return cls(_registry_ref=cls._line_text(ref, "ImageBuilder.from_registry ref"))

    @classmethod
    def from_dockerfile(cls, content: str) -> Self:
        return cls(_dockerfile=cls._require_text(content, "ImageBuilder.from_dockerfile content", strip=True))

    def pip_install(self, *packages: str) -> Self:
        if not packages:
            raise create_error(
                "ImageBuilder.pip_install requires at least one package",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        cleaned = tuple(self._line_text(package, "ImageBuilder.pip_install package") for package in packages)
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
        cleaned = tuple(self._line_text(command, "ImageBuilder.run_commands command") for command in commands)
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
                    self._env_name(name_or_values, "ImageBuilder.env name"),
                    self._require_text(value, "ImageBuilder.env value"),
                ),
            )
        elif isinstance(name_or_values, Mapping):
            # A mapping carries its values: a second argument would be dropped.
            if value is not None:
                raise create_error(
                    "ImageBuilder.env takes a name and a value, or a mapping of names to values",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )
            entries = tuple(
                (
                    self._env_name(key, "ImageBuilder.env key"),
                    self._require_text(entry_value, "ImageBuilder.env value"),
                )
                for key, entry_value in name_or_values.items()
            )
            # Keys are stripped: two that collide afterwards would silently keep one.
            seen: set[str] = set()
            for key, _ in entries:
                if key in seen:
                    raise create_error(
                        f"ImageBuilder.env key {json.dumps(key, ensure_ascii=False)} appears more than once after stripping",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                seen.add(key)
        else:
            raise create_error(
                "ImageBuilder.env takes a name and a value, or a mapping of names to values",
                400,
                "BAD_REQUEST",
                "SDK",
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
            _steps=self._steps + (f"WORKDIR {self._line_text(path, 'ImageBuilder.workdir path')}",),
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

    @classmethod
    def _line_text(cls, value: object, label: str) -> str:
        """Text rendered raw into one Dockerfile line, stripped: a line break
        would end the instruction and start another, so no control character
        (C0 or DEL) is allowed. TypeScript refuses the same values."""
        text = cls._require_text(value, label, strip=True)
        if any(char < " " or char == "\x7f" for char in text):
            raise create_error(
                f"{label} {json.dumps(text, ensure_ascii=False)} must not contain a control character",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return text

    @classmethod
    def _env_name(cls, value: object, label: str) -> str:
        """An env name, stripped: it renders as ``ENV name=value``, so ``=``,
        whitespace (Python's set) or a control character (C0 or DEL) would
        change the instruction, or add one. TypeScript refuses the same names."""
        name = cls._require_text(value, label, strip=True)
        if any(char == "=" or char.isspace() or char < " " or char == "\x7f" for char in name):
            raise create_error(
                f"{label} {json.dumps(name, ensure_ascii=False)} must not contain '=', whitespace or a control character",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return name

    @staticmethod
    def _require_text(value: object, label: str, *, strip: bool = False) -> str:
        """A string that is not blank (Python's whitespace set), stripped when
        it is rendered raw; anything else is :class:`BadRequestError`."""
        if not isinstance(value, str) or not value.strip():
            raise create_error(f"{label} must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        # A Dockerfile is UTF-8 text: a surrogate has no UTF-8 form, and the two
        # SDKs would render it differently. TypeScript refuses it the same way.
        if any("\ud800" <= char <= "\udfff" for char in value):
            raise create_error(f"{label} must not contain a lone surrogate", 400, "BAD_REQUEST", "SDK")
        return value.strip() if strip else value
