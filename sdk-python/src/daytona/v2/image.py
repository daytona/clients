# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, overload

from typing_extensions import Self

from ._image_source import LocalSource, local_source
from ._image_text import dockerfile_escape, ends_with_continuation, require_text
from .errors import create_error

_ESCAPE_DIRECTIVE_REFUSED: Final = (
    "ImageBuilder.env cannot keep values literal under a Dockerfile escape directive other than a backslash"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageBuilder:
    """A declarative image source: build one with :meth:`from_registry` or :meth:`from_dockerfile`."""

    _registry_ref: str | None = None
    _dockerfile: str | None = None
    _steps: tuple[str, ...] = ()
    _local_sources: tuple[LocalSource, ...] = ()

    def __post_init__(self) -> None:
        if (self._registry_ref is None) == (self._dockerfile is None):
            raise create_error(
                "an ImageBuilder starts from ImageBuilder.from_registry(...) or ImageBuilder.from_dockerfile(...)",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        # Checked as each step is added: a base, or an earlier step, ending in a
        # line continuation would swallow it (and every later step) silently.
        # Only a Dockerfile base can select an escape character other than `\`;
        # a raw step ending in `\` is refused where it is added.
        if self._steps and self._dockerfile is not None:
            if ends_with_continuation(self._dockerfile):
                raise create_error(
                    "ImageBuilder.from_dockerfile content ends with a line continuation; a step added after it would be joined to its last instruction",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )
            if len(self._steps) > 1 and ends_with_continuation(
                "\n".join((self._dockerfile, *self._steps[:-1]))
            ):
                raise create_error(
                    "ImageBuilder step ends with the Dockerfile's escape character; the next step would be joined to it",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )

    # One rule in both SDKs: every value must be a string that is not blank by
    # Python's whitespace set, and text rendered raw into a Dockerfile line
    # (the registry ref, the Dockerfile, commands, packages, env keys, the
    # workdir) is stripped with that set; an env value is kept literal, with
    # dollar signs escaped. TypeScript applies the same rule.

    @classmethod
    def from_registry(cls, ref: str, /) -> Self:
        return cls(_registry_ref=cls._line_text(ref, "ImageBuilder.from_registry ref"))

    @classmethod
    def from_dockerfile(cls, content: str, /) -> Self:
        return cls(_dockerfile=require_text(content, "ImageBuilder.from_dockerfile content", strip=True))

    @classmethod
    def debian_slim(cls, python_version: str | None = None) -> Self:
        if python_version is not None and not isinstance(python_version, str):
            raise create_error(
                "ImageBuilder.debian_slim python_version must be a string", 400, "BAD_REQUEST", "SDK"
            )
        version = "3.13" if python_version is None else python_version.strip()
        if version not in ("3.10", "3.11", "3.12", "3.13", "3.14"):
            raise create_error(
                "ImageBuilder.debian_slim python_version must be one of 3.10, 3.11, 3.12, 3.13, 3.14 "
                f"(got {json.dumps(version, ensure_ascii=False)})",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return cls.from_registry(f"python:{version}-slim-bookworm").run_commands(
            "apt-get update",
            "apt-get install -y gcc gfortran build-essential",
            "pip install --upgrade pip",
            "echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections",
        )

    def add_local_file(self, local_path: str | os.PathLike[str], remote_path: str, /) -> Self:
        source = local_source(local_path, "file", len(self._local_sources) + 1)
        remote = self._line_text(remote_path, "ImageBuilder.add_local_file remote_path")
        # The appended name is the file's own, not a Dockerfile word: BuildKit
        # runs its shell-word lexer over COPY destinations, so `\`, `$` and
        # both quotes are escaped.
        filename = re.sub(r"""([\\$'"])""", r"\\\1", source.path.name)
        return self._copy(source, remote + filename if remote.endswith("/") else remote)

    def add_local_dir(self, local_path: str | os.PathLike[str], remote_path: str, /) -> Self:
        source = local_source(local_path, "dir", len(self._local_sources) + 1)
        return self._copy(source, self._line_text(remote_path, "ImageBuilder.add_local_dir remote_path"))

    def pip_install_from_requirements(self, requirements_txt: str | os.PathLike[str], /) -> Self:
        source = local_source(requirements_txt, "file", len(self._local_sources) + 1)
        return self._copy(source, "/.requirements.txt").run_commands(
            "python -m pip install -r /.requirements.txt"
        )

    def _copy(self, source: LocalSource, remote: str) -> Self:
        paths = json.dumps([source.archive_path, remote], ensure_ascii=False)
        return replace(
            self,
            _steps=self._steps + (f"COPY {paths}",),
            _local_sources=self._local_sources + (source,),
        )

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
        return replace(
            self,
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
                    self._env_value(value, "ImageBuilder.env value"),
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
                    self._env_value(entry_value, "ImageBuilder.env value"),
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
        if self._dockerfile is not None and dockerfile_escape(self._dockerfile) != "\\":
            raise create_error(_ESCAPE_DIRECTIVE_REFUSED, 400, "BAD_REQUEST", "SDK")
        return replace(
            self,
            # ensure_ascii=False: TS renders with JSON.stringify, which leaves non-ASCII
            # as is; keep Dockerfile output consistent across SDKs and preserve values.
            _steps=self._steps
            + tuple(f"ENV {key}={self._env_value_literal(entry_value)}" for key, entry_value in entries),
        )

    def workdir(self, path: str, /) -> Self:
        return replace(
            self,
            _steps=self._steps + (f"WORKDIR {self._line_text(path, 'ImageBuilder.workdir path')}",),
        )

    def user(self, name: str, /) -> Self:
        return replace(
            self, _steps=self._steps + (f"USER {self._line_text(name, 'ImageBuilder.user name')}",)
        )

    def cmd(self, *args: str) -> Self:
        return self._json_command("CMD", args)

    def entrypoint(self, *args: str) -> Self:
        return self._json_command("ENTRYPOINT", args)

    def _json_command(self, instruction: str, args: tuple[str, ...]) -> Self:
        label = f"ImageBuilder.{instruction.lower()} args"
        if not args:
            raise create_error(f"{label} requires at least one value", 400, "BAD_REQUEST", "SDK")
        cleaned = tuple(require_text(arg, label) for arg in args)
        return replace(
            self, _steps=self._steps + (f"{instruction} {json.dumps(cleaned, ensure_ascii=False)}",)
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
        text = require_text(value, label, strip=True)
        if any(char < " " or char == "\x7f" for char in text):
            raise create_error(
                f"{label} {json.dumps(text, ensure_ascii=False)} must not contain a control character",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        # A raw line would continue onto the next instruction, and BuildKit's
        # word lexer runs over JSON-form COPY paths too, where it is dropped.
        if text.endswith("\\"):
            raise create_error(
                f"{label} {json.dumps(text, ensure_ascii=False)} must not end with a backslash "
                "(the Dockerfile parser reads it as an escape)",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return text

    @classmethod
    def _env_value(cls, value: object, label: str) -> str:
        """An env value, kept as given: a control character (C0 or DEL) has no
        literal form in a double-quoted Dockerfile word (Docker keeps ``\\n``
        as two characters), so it is refused. TypeScript refuses the same
        values."""
        text = require_text(value, label)
        # The value is not echoed: it may be a secret.
        if any(char < " " or char == "\x7f" for char in text):
            raise create_error(
                f"{label} must not contain a control character",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return text

    @staticmethod
    def _env_value_literal(value: str) -> str:
        """An env value as a double-quoted Dockerfile word that the image keeps
        literally: ``"`` and ``\\`` are escaped (as in JSON) and so is ``$``, so
        nothing is substituted. ``ensure_ascii=False``: non-ASCII is kept as
        is, as TypeScript renders it; both SDKs render the same bytes."""
        return json.dumps(value, ensure_ascii=False).replace("$", "\\$")

    @classmethod
    def _env_name(cls, value: object, label: str) -> str:
        """An env name, stripped: it renders as ``ENV name=value``, so ``=``,
        whitespace (Python's set) or a control character (C0 or DEL) would
        change the instruction, or add one. TypeScript refuses the same names."""
        name = require_text(value, label, strip=True)
        if any(char == "=" or char.isspace() or char < " " or char == "\x7f" for char in name):
            raise create_error(
                f"{label} {json.dumps(name, ensure_ascii=False)} must not contain '=', whitespace or a control character",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return name
