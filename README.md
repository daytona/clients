# Daytona SDKs — V2 (TypeScript + Python)

The Daytona V2 client libraries and the `daytona` CLI. Package names are
`@daytona/sdk` (npm) and `daytona` / module `daytona` (PyPI); the CLI lives in
[`cli/`](cli/).

The SDKs expose Daytona's API through resource handles and namespaces for
processes, files, Git, code execution, ports, LSP and computer use.

## Layout

```
.
├── cli/             daytona CLI (Go)
├── contract/        OpenAPI contracts the SDKs and CLI generate their wire types from
├── sdk-python/      daytona (module daytona) — root client + daytona.v2 (async-native, sync surface generated) + daytona.v0 (bundled at publish)
├── sdk-typescript/  @daytona/sdk            — root client + /v2 + /v0 (bundled at publish), ESM + CJS
├── upgrade/         daytona-upgrade          — 0.x → 2.x migration tool: npm (upgrade/typescript/) + PyPI (upgrade/python/) packages,
│                    one shared mapping (mapping/), the generated MIGRATION.md
├── examples/        runnable examples for both SDKs
├── e2e/             live API e2e, run.sh (needs DAYTONA_API_KEY)
├── scripts/         bundle-v0.sh            — publish-time v0 namespace bundling; its output is never committed
│                    (+ v0-sdk-version, the pinned 0.x tag the v0 namespace vendors;
│                    resolve-v0-tag.sh, bundle_v0_python.py, check-no-bundled-v0.sh, changelog/)
└── docs/            RELEASING.md, VERSIONING.md, PREPARING_YOUR_CHANGES.md, adr/ (architecture decision records)
```

## The shared V2 model (both languages, one shape)

- **Serializable handles.** `Sandbox` and `ProcessHandle` reconstruct from
  plain JSON — built for RL rollouts that fan work across workers. A
  rehydrated `kind:"code"` process handle can still replay logs and wait/kill,
  but it does not reconstruct a decomposed `InterpreterResult` on another
  worker.
- **Detached vs awaited, typed apart.** `start()` → handle; `run()` → result.
- **Shareable volumes.** The v2 client exposes `volume.create/get/list/delete`,
  with explicit `iter()` traversal for all pages. Attach volumes using the
  `volumes` create option and refresh mounted data with `sandbox.volumes.pull()`.
  The CLI exposes the `volume` command group and `--volume` on Linux and macOS
  sandbox creates. See the [volume examples](examples/README.md#shared-volume-persistence).
- **Everything replayable.** Cursor-paged `logs()`, SSE streaming with resume,
  `wait()` long-poll; nothing requires having been attached.
- **Filesystem watches are SSE-backed.** Both SDKs expose start-gated directory
  watches over `GET /files/watch`; TS callbacks are awaited sequentially and
  Python exposes a drainable event queue.
- **Filesystem pre-signed transfer URLs are minted by the API.**
  Both SDKs POST `{method, path, expires_at}` to
  `/sandboxes/{id}/files/sign` and compose the download/upload
  link from their own toolbox base plus the returned `signature` / `expires_at`.
  `sandbox.rotate_signing_key()` /
  `sandbox.rotateSigningKey()` POST `/sandboxes/{id}/signing-key/rotate`, receive
  204 with no body and invalidate every outstanding link for the sandbox
  shortly after. `expires_in_seconds` /
  `expiresInSeconds` must be from 1 to 604800 (7 days) before any request —
  never-expiring links do not exist. A per-sandbox proxy override may be
  given either as a concrete sandbox URL
  or as the `{sandboxId}` / `{sandbox_id}` template — the placeholder is a
  path segment (`https://proxy.example/sandboxes/{sandboxId}`); a placeholder in
  the scheme, host, query or fragment is rejected, and the id is percent-encoded into it identically in
  both SDKs. Direct toolbox URLs are not supported here: pre-signed URLs
  require the sandbox's toolbox proxy URL.
- **Interpreter = code executions** (`kind:"code"`): Python and TS/JS with setup progress on the `setup`
  channel. Timeouts are CLIENT-side (on expiry
  the SDK interrupts the kernel like ^C). Rich `results[]` / `logs` / `error`
  decomposition comes from `runCode()` / `interpreter.run()`, not from
  rehydrating a serialized code-process handle elsewhere. If an explicit named
  context vanished, the SDK surfaces the typed `SESSION_NOT_FOUND` error; the
  caller recreates the context and chooses what to rerun.
- **Sandbox service namespaces stay mirrored.** Both SDKs expose `ports`, `lsp`,
  and `computer-use` alongside `process`, `fs`, `git`, `interpreter`, and
  `pty`, with matching wire fields and mirrored tests.
- **Network policy (Linux and macOS sandboxes) uses explicit compare-and-swap.** Both SDKs expose an
  additive create-time `network_policy` / `networkPolicy` field and
  `get_network_policy` / `update_network_policy` / `reset_network_policy`
  (Python) plus `getNetworkPolicy` / `updateNetworkPolicy` /
  `resetNetworkPolicy` (TypeScript) for `GET|PUT /sandboxes/{id}/network-policy`.
  The read side carries desired vs applied policy, the quoted desired ETag,
  `applied_start_id` / `applied_revision`, and pending/failed metadata; writes
  use full replacement with caller-supplied `If-Match`, and reset sends a literal JSON
  `null`. The serializers preserve omitted vs `null` vs `[]` for domain lists;
  with domains present they omit `block_all` unless the caller sets it, and an
  explicit false is rejected locally as
  `NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL`; they also keep the read-side
  `version: 2` field out of write input. The API rejects unsupported sandbox
  configurations.
- **Runtime-managed sandbox secret attachments use the same compare-and-swap shape.**
  Both SDKs expose sandbox-handle `get_secrets` / `replace_secrets`
  (Python) and `getSecrets` / `replaceSecrets` (TypeScript), plus root-client
  `get_sandbox_secrets` / `replace_sandbox_secrets` and `getSandboxSecrets` /
  `replaceSandboxSecrets`, for `GET|PUT /sandboxes/{id}/secrets`. Responses carry
  the desired secret names, desired/applied revision state, the quoted desired
  ETag, the apply `status` (`pending`/`applied`/`failed`) with its `error`, and
  capture metadata; writes are full replacement only,
  use caller-supplied `If-Match`, and never send or return secret values.
- **Typed errors from one catalog.** Status-level bases → code-specific
  subclasses with `code`/`source`; each SDK's constants are drift-tested
  against the contract artifact `contract/toolbox-openapi.json`.
- **Mac reservations buy physical Macs.** Both SDKs reserve one Mac per
  reservation, individually or in bulk. Sandbox CPU, memory, and disk sizes
  are per-create inputs. `MacModel` exposes `max_sandboxes` and `default_size`.
- Signals use bare names on the surface (`TERM`, `INT`, …), `SIG*` on the wire.

## Verification

```bash
(cd sdk-python && uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest -x -q)
(cd sdk-typescript && npm install && npm run typecheck && npm test && npm run build)
(cd cli && go vet ./... && go test ./...)
scripts/test-upgrade.sh   # both daytona-upgrade packages + mapping / guide drift
```

The TypeScript suite also runs a live toolbox test when
`DAYTONA_SDKTS_TOOLBOX_URL` points at a reachable sandbox toolbox, and skips it
otherwise. The end-to-end suites (`e2e/`, run with `e2e/run.sh`) run against
a Daytona deployment when `DAYTONA_API_KEY` is set. Details: each package's
`DEVELOPMENT.md`.

Releases: [RELEASING.md](docs/RELEASING.md) · [VERSIONING.md](docs/VERSIONING.md) ·
[CHANGELOG.md](CHANGELOG.md). Every PR that changes an SDK adds one changelog
fragment under [`changelog.d/`](changelog.d/) (not a line in `CHANGELOG.md`);
see [`changelog.d/README.md`](changelog.d/README.md).

## License

The SDKs are licensed under [Apache-2.0](LICENSE); the CLI (`cli/`) under
[AGPL-3.0](cli/LICENSE). See [COPYRIGHT](COPYRIGHT) and
[CONTRIBUTING.md](CONTRIBUTING.md).
