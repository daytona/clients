# SDK examples

Runnable examples for the Daytona SDKs (Python `daytona` / TypeScript `@daytona/sdk`).

Each example exists in both languages and uses a Daytona deployment with the same
environment variables (see [Environment](#environment)).

To follow an **existing** build without buffering a transcript, use
[`python/follow_build_logs.py`](./python/follow_build_logs.py) or
[`typescript/follow-build-logs/index.ts`](./typescript/follow-build-logs/index.ts).
Both take `image|sandbox ID` and an optional checkpoint (Python: `--cursor`,
TypeScript: third argument). They print output incrementally, report typed
state/gap events separately, and leave the build running when observation stops.
The `build-logs` examples return build output as text; the followers use live events
and checkpoints. These opt-in followers are not part of the all-examples run.
The TypeScript follower targets Node.js, awaits each stdout write to honor slow
readers, and stops observation quietly if a pipe closes early (`EPIPE`).

```
examples/
├── typescript/
│   ├── _common.ts            shared client/env helpers
│   ├── exec-command/index.ts
│   ├── file-operations/index.ts
│   ├── git/index.ts
│   ├── lifecycle/index.ts
│   ├── snapshots/index.ts
│   ├── dynamic-build/index.ts
│   ├── dockerfile-sandbox/index.ts
│   ├── build-logs/index.ts
│   ├── follow-build-logs/index.ts  opt-in existing-build follower (Node.js)
│   ├── follow-build-logs/output.ts backpressure-aware CLI output helper
│   ├── follow-build-logs/output.test.ts  local output tests
│   ├── examples.test.ts      offline tests (fake client, see test-support/)
│   ├── pagination/index.ts
│   ├── volumes/index.ts
│   └── secrets-env/index.ts
├── python/
│   ├── _common.py            shared client/env helpers
│   ├── exec_command.py
│   ├── file_operations.py
│   ├── git_ops.py
│   ├── lifecycle.py
│   ├── snapshots.py
│   ├── dynamic_build.py
│   ├── dockerfile_sandbox.py
│   ├── build_logs.py
│   ├── follow_build_logs.py   opt-in existing-build follower
│   ├── pagination.py
│   ├── volumes.py
│   ├── secrets_env.py
│   └── test_examples.py      offline tests (fake client)
├── volumes_share.{ts,py}     persistent-volume round-trip (see below)
└── run-examples.sh           run all examples (or `check` them statically)
```

## Example coverage

The examples exercise the V2 SDK surface in both languages.

| Area | Example | Notes |
|---|---|---|
| process | `exec-command` | ✅ `process.run()` plus `interpreter.run()` code execution. |
| files | `file-operations` | ✅ `fs.*` single-file upload, list, search, replace and download. |
| git | `git` | ✅ clone/add/commit/branches/status/push/pull. |
| lifecycle | `lifecycle` | ✅ create/get/list/run/delete. |
| images | `snapshots` | ✅ `images.build({ dockerfile })`, image-status polling, then create from the registered image. |
| dynamic build | `dynamic-build` | ✅ Create sandboxes from inline Dockerfiles, read their build logs, and verify the files produced by each Dockerfile. Reports create durations without assuming a speedup. Requires image-build support. |
| dockerfile sandbox | `dockerfile-sandbox` | ✅ The happy path — one call builds an inline Dockerfile and hands back a **running** sandbox, then proves the baked script, `ENV`, `WORKDIR` and data file all survived into the sandbox, and round-trips a file at runtime. `DAYTONA_KEEP=1` leaves it running to poke at. Requires image-build support. |
| build logs | `build-logs` | ✅ Prints Dockerfile build output (`sandbox.build_logs()` / `buildLogs()`), including each `RUN` step's stdout and a failed build's reason. Also reads logs for a second sandbox created from the same Dockerfile. Requires image-build support. |
| pagination | `pagination` | ✅ `images.iter()` for lazy all-pages traversal plus manual `images.list(...)` cursor control. |
| `secrets` (+ env) | `secrets-env` | ✅ `secret.set/iter/delete` (a run-scoped name) + attach via `secrets: [name]`, alongside per-sandbox `envVars`. Shows the key contrast — **env vars carry real values into the sandbox; secrets show only a placeholder there** (the real value is used only in outgoing requests to the allowed domains). |
| volumes | `volumes` | `volume.get(create)` and mounts at different paths, including a subpath. See also `volumes_share.*`. |

Not covered here: LSP servers and network settings.

## Environment

| Var | Default | Notes |
|---|---|---|
| `DAYTONA_API_URL` | `https://api.daytona.com` | Daytona API |
| `DAYTONA_API_KEY` | — (required) | `dtn_...` API key |
| `DAYTONA_IMAGE` | `python:3.12-slim` | registry image the examples start from. A sandbox is created from a project image, so the examples that create one directly register it once as `examples-<image>-<digest>` and reuse that registration. The build and volume examples use the registry image itself as their base (`FROM` / `ImageBuilder.from_registry`) |


Mint a project API key from the dashboard (it needs sandbox read/write permissions).

## Running

```bash
cd examples

# Build/link deps once (uses the local SDK via file:../sdk-typescript).
npm install
(cd ../sdk-python && uv pip install -e . || pip install -e .)

# TypeScript
DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
  npx tsx typescript/exec-command/index.ts     # or: npm run exec-command

# Python
DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
  PYTHONPATH=python python python/exec_command.py
```

### Run them all

```bash
DAYTONA_API_KEY=dtn_... \
  ./run-examples.sh            # both languages
./run-examples.sh ts           # typescript only
./run-examples.sh py           # python only

# Include the opt-in volume examples:
DAYTONA_RUN_VOLUMES=1 DAYTONA_API_KEY=dtn_... ./run-examples.sh
```


### Static validation (no API access needed)

`tsc --noEmit` and `mypy --strict` over every example, plus offline tests
(`typescript/examples.test.ts`, `python/test_examples.py`) that run the
exec-command, file-operations, git, lifecycle, secrets-env, dynamic-build,
build-logs and volumes-share examples (and the Python dockerfile-sandbox)
against a fake client and check their exit codes and cleanup. CI runs the same
command (`Examples CI`). `check` uses `../sdk-python/.venv` when it exists, so
install the SDK's dev extras there:

```bash
(cd ../sdk-python && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]")
./run-examples.sh check
```

## Shared-volume persistence

`volumes_share.py` and `volumes_share.ts` verify a three-sandbox round-trip:
A writes a file and is deleted, B reads it and appends a line before deletion,
then C reads both writers' data. Writes synchronize asynchronously; the scripts
wait `EXAMPLE_SYNC_WAIT_SECONDS` seconds (default 20) before deleting each writer.
Increase it if synchronization needs more time. `DAYTONA_KEEP=1` retains the
resources for inspection. Both scripts use `https://api.daytona.com` by default.

```bash
cd examples
DAYTONA_API_KEY=dtn_... python volumes_share.py
DAYTONA_API_KEY=dtn_... npm run volumes-share
```
