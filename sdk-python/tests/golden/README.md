# Python SDK golden process-contract tests

This suite locks the current live behavior of the legacy Python SDK process surface against the production daemon.

## Guarantees

- `process.exec` sync + async behavior, including current timeout/error semantics.
- Session CRUD, command execution, logs, input, entrypoint shapes, and the current `cwd` / `env` ignore bug.
- `code_run` sync + async behavior across Python, JavaScript, and TypeScript, including chart artifacts.
- PTY sync + async handle behavior, resize/list/info shapes, connect, echo, and kill exit semantics.
- Code interpreter sync + async context, persistence, error, and timeout behavior.
- Current SDK error translation quirks, including the raw generated-client exception leaked by `code_run` unsupported-language failures.

## Run

```bash
cd /home/ubuntu/daytona/clients
source /tmp/opencode/daytona.env
yarn nx run sdk-python:test:golden
```

Or directly:

```bash
poetry run python -m pytest sdk-python/tests/golden -m golden -v -s --timeout=240
```

## Rule

Never relax an assertion to make a refactor pass.
If the refactor changes behavior, either fix the refactor or deliberately update the contract after new live evidence says the behavior really changed.
