#!/usr/bin/env python3
"""secrets_env — project secrets and per-sandbox environment variables.

Daytona ships two distinct ways to get configuration/credentials into a
sandbox at create time. This example demonstrates both and, crucially, how they
differ:

  1. ENV VARS  (``env={"NAME": value}``)
     Real values carried verbatim into the sandbox. Readable inside the sandbox
     as ordinary environment variables. Use for non-sensitive config
     (LOG_LEVEL, FEATURE_FLAGS, an app's own settings).

  2. SECRETS   (``daytona.secrets.set(name, value)`` + ``secrets=[name]``)
     Project-scoped and write-only. The sandbox sees only a placeholder in
     an env var of the same name; the real value is never readable inside the
     sandbox and is used only in outgoing requests to the secret's allowed
     domains. Use for API keys / tokens you don't want readable from inside
     the sandbox.

So: env vars are verifiable by reading them inside the sandbox; secrets are
not (you see only the placeholder). This example asserts exactly that
contrast.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/secrets_env.py
"""

from __future__ import annotations

import sys

from _common import RUN, client, failed, info, passed, require_started, sandbox_image, section

from daytona.v2 import Sandbox


def main() -> int:
    daytona = client()
    ok = True

    # A project secret is write-only: the API never returns its value.
    # The name is run-scoped, so the example never touches another secret in
    # the project; it is also the env var the secret is exposed as.
    env_var_name = f"EXAMPLE_API_KEY_{RUN.upper()}"
    secret_value = f"sk-secret-{RUN}"

    secret_created = False
    sandbox: Sandbox | None = None
    try:
        section("secrets.set(name, value) — create a project secret")
        daytona.secrets.set(
            env_var_name,
            secret_value,
            # allowed_domains: where the secret's value may be sent
            allowed_domains=["api.example.com"],
            description="demo secret for the secrets_env example",
            # Never overwrite an existing secret: cleanup deletes only ours.
            if_not_exists=True,
        )
        secret_created = True
        info(f"set secret {env_var_name} (value is write-only; never returned)")

        section("secrets.iter() — metadata only, never values")
        found = any(s.name == env_var_name for s in daytona.secrets.iter())
        info(f"{env_var_name} present={found}")
        if not found:
            failed(f"secret {env_var_name} is missing from secrets.iter()")
            ok = False

        section("create a sandbox with BOTH env vars and the secret attached")
        sandbox = daytona.create(
            sandbox_image(daytona),
            name=f"secrets-env-{RUN}",
            env={"APP_ENV": "production", "LOG_LEVEL": "debug", "GREETING": f"hello-{RUN}"},
            secrets=[env_var_name],
            wait_seconds=200,
        )
        info(f"created sandbox id={sandbox.id} state={sandbox.state}")
        if not require_started(sandbox):
            return 1

        section("env vars ARE readable inside the sandbox (real values)")
        for name, expected in (
            ("APP_ENV", "production"),
            ("LOG_LEVEL", "debug"),
            ("GREETING", f"hello-{RUN}"),
        ):
            res = sandbox.process.run(f'printf %s "${name}"')
            got = (res.output or "").strip()
            if res.exit_code == 0 and got == expected:
                passed(f'${name} = "{got}"')
            else:
                failed(f'${name} = "{got}" (expected "{expected}", exit={res.exit_code})')
                ok = False

        section("the SECRET is NOT the real value inside the sandbox (placeholder only)")
        res = sandbox.process.run(f'printf %s "${env_var_name}"')
        in_sandbox = (res.output or "").strip()
        # The env var exists (so apps can reference it), but it holds an opaque
        # placeholder, not the secret we set. The real value is used only in outgoing requests.
        if res.exit_code != 0:
            failed(f"reading ${env_var_name} failed (exit={res.exit_code})")
            ok = False
        elif in_sandbox == secret_value:
            failed(f"${env_var_name} leaked the real secret value into the sandbox!")
            ok = False
        elif in_sandbox:
            passed(f"${env_var_name} in the sandbox is a placeholder, not the secret value")
        else:
            failed(f"${env_var_name} is not set inside the sandbox (expected a placeholder)")
            ok = False
    finally:
        section("cleanup")
        if sandbox is not None:
            try:
                daytona.delete(sandbox)
                info(f"deleted sandbox {sandbox.id}")
            except Exception as e:
                failed(f"delete sandbox {sandbox.id} failed: {e}")
                ok = False
        if secret_created:
            try:
                daytona.secrets.delete(env_var_name)
                info(f"deleted secret {env_var_name}")
            except Exception as e:
                # A secret left behind in the project fails the example.
                failed(f"delete secret {env_var_name} failed: {e}")
                ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
