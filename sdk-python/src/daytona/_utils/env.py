# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import warnings

from dotenv import dotenv_values


class DaytonaEnvReader:
    """Reads DAYTONA_* env vars on demand without polluting os.environ.

    Parses .env and .env.local once at construction.

    `get` applies the precedence runtime env → .env.local → .env. That precedence must NOT
    be used for values that decide where a credential is sent, because the dotenv files come
    from the working directory; use `get_from_process_env` for those.
    """

    def __init__(self) -> None:
        self._env_local_vars: dict[str, str] = self._load(".env.local")
        self._env_vars: dict[str, str] = self._load(".env")

    def get(self, name: str) -> str | None:
        self._check_name(name)
        # 1. Runtime env
        val = os.environ.get(name)
        if val is not None:
            return val
        # 2. .env.local, 3. .env
        return self.get_from_file(name)

    def get_from_process_env(self, name: str) -> str | None:
        """Read `name` from the process environment only, never from .env / .env.local.

        The dotenv files are read from the current working directory, which is not
        necessarily authored by whoever runs the process. Anything that determines the
        host a credential is sent to must be resolved through this method so that a
        file in the working directory cannot redirect the SDK.
        """
        self._check_name(name)
        return os.environ.get(name)

    def get_from_file(self, name: str) -> str | None:
        """Read `name` from .env.local / .env only, ignoring the process environment."""
        self._check_name(name)
        if name in self._env_local_vars:
            return self._env_local_vars[name]
        return self._env_vars.get(name)

    @staticmethod
    def _check_name(name: str) -> None:
        if not name.startswith("DAYTONA_"):
            raise ValueError(f"DaytonaEnvReader: variable name must start with 'DAYTONA_', got '{name}'")

    @staticmethod
    def _load(path: str) -> dict[str, str]:
        parsed = dotenv_values(path)
        return {k: v for k, v in parsed.items() if k.startswith("DAYTONA_") and v is not None}


def warn_if_dotenv_api_url_ignored(env_reader: DaytonaEnvReader, api_url: str) -> None:
    """Warn when a dotenv file asked for an API endpoint that was not used.

    Staying silent when the file value matches the endpoint in use keeps the documented
    `.env` layout quiet, while a file that would have changed the destination is reported.
    """
    for name in ("DAYTONA_API_URL", "DAYTONA_SERVER_URL"):
        file_value = env_reader.get_from_file(name)
        if file_value and file_value != api_url:
            warnings.warn(
                f"`{name}` set in a .env or .env.local file was ignored:"
                + " the Daytona API endpoint is never read from dotenv files, because the working"
                + f" directory is not always authored by you. Using `{api_url}` instead."
                + f" To change the endpoint, pass `api_url` to `DaytonaConfig` or set `{name}`"
                + " in the environment of the process.",
                UserWarning,
                stacklevel=3,
            )
