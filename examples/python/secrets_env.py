#!/usr/bin/env python3
"""secrets_env — project secrets and per-sandbox environment variables.

Daytona ships two distinct ways to get configuration/credentials into a
sandbox at create time. This example demonstrates both and, crucially, how they
differ:

  1. ENV VARS  (``env_vars={"NAME": value}``)
     Real values carried verbatim into the sandbox. Readable inside the sandbox
     as ordinary environment variables. Use for non-sensitive config
     (LOG_LEVEL, FEATURE_FLAGS, an app's own settings).

  2. SECRETS   (``daytona.secret.set(name, value)`` + ``secrets=[name]``)
     Project-scoped and write-only. The sandbox sees only a placeholder in
     an env var of the same name; the real value is never readable inside the
     sandbox and is used only in outgoing requests to the secret's allowed
     domains. Use for API keys / tokens you don't want readable from inside
     the sandbox.

So: env vars are verifiable by reading them inside the sandbox; secrets are
not (you see only the placeholder). This example asserts exactly that
contrast.

Run (from examples):
DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \\
      python python/secrets_env.py
"""

from __future__ import annotations

import sys

from _common import IMAGE, RUN, client, failed, info, passed, section

from daytona.v2 import Image


def main() -> int:
    daytona = client()

    # A project secret is write-only: the API never returns its value.
    # The env var the secret is exposed as (a name, never the value).
    env_var_name = "EXAMPLE_API_KEY"
    secret_value = f"sk-secret-{RUN}"

    section("secret.set(name, value) — create/update a project secret")
    daytona.secret.set(
        env_var_name,
        secret_value,
        # allowed_domains: where the secret's value may be sent
        allowed_domains=["api.example.com"],
        description="demo secret for the secrets_env example",
    )
    info(f"set secret {env_var_name} (value is write-only; never returned)")

    section("secret.list() — metadata only, never values")
    secrets = daytona.secret.list()
    found = any(s.name == env_var_name for s in secrets.items)
    info(f"listed {len(secrets.items)} secret(s); {env_var_name} present={found}")

    sandbox = None
    try:
        section("create a sandbox with BOTH env vars and the secret attached")
        sandbox = daytona.create(Image.base(IMAGE), name=f"secrets-env-{RUN}",
            env={"APP_ENV": "production", "LOG_LEVEL": "debug", "GREETING": f"hello-{RUN}"},
            secrets=[env_var_name],
            timeout=200,)
        info(f"created sandbox id={sandbox.id} state={sandbox.state}")

        section("env vars ARE readable inside the sandbox (real values)")
        for name, expected in (
            ("APP_ENV", "production"),
            ("LOG_LEVEL", "debug"),
            ("GREETING", f"hello-{RUN}"),
        ):
            res = sandbox.process.run(f'printf %s "${name}"')
            got = (res.output or "").strip()
            if got == expected:
                passed(f'${name} = "{got}"')
            else:
                failed(f'${name} = "{got}" (expected "{expected}")')

        section("the SECRET is NOT the real value inside the sandbox (placeholder only)")
        in_sandbox = (
            sandbox.process.run(f'printf %s "${env_var_name}"').output or ""
        ).strip()
        # The env var exists (so apps can reference it), but it holds an opaque
        # placeholder, not the secret we set. The real value is used only in outgoing requests.
        if in_sandbox and in_sandbox != secret_value:
            passed(f"${env_var_name} in the sandbox is a placeholder, not the secret value")
        elif in_sandbox == secret_value:
            failed(f"${env_var_name} leaked the real secret value into the sandbox!")
        else:
            info(f"${env_var_name} not present inside the sandbox; OK")
    finally:
        section("cleanup")
        if sandbox is not None:
            try:
                daytona.delete(sandbox)
                info(f"deleted sandbox {sandbox.id}")
            except Exception as e:
                info(f"delete sandbox failed: {e}")
        try:
            daytona.secret.delete(env_var_name)
            info(f"deleted secret {env_var_name}")
        except Exception as e:
            info(f"delete secret failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
