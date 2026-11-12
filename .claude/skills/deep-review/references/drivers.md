# Boundary driver recipes

Run from an isolated writable copy of the pinned repository, with each
driver and log in a unique local review directory. Preserve `sdk-python/`,
`sdk-typescript/`, `cli/`, `contract/`, and `scripts/` relative paths. Use test
credentials, ephemeral loopback ports, isolated CLI profiles, and bounded
test contexts. Never let a never-responder hang the review process itself.

## Python: installed package, async and sync

```bash
cd sdk-python
uv venv
uv pip install -e ".[dev]"
.venv/bin/python -m pytest -x -q
.venv/bin/python -m pytest tests/test_sync_surface.py -q
# Put probe.py in the review directory; import daytona, not copied helpers.
.venv/bin/python "$REVIEW_DIR/probe.py"
# For async source changes, regenerate only in this disposable copy:
.venv/bin/python gen/gen_sync.py
```

Use `respx` at the httpx boundary following existing tests, or a local HTTP
server for transport behavior. Import `Daytona` / `AsyncDaytona` from the
root and operate through `.v2` for public-entry probes. For lower-level
adapters use the actual package implementation, not an imitation.

Give a fixture a request recorder and configurable response sequence. Run
equivalent calls through sync and async public methods. Assert body/query
placement, error class and extras, output channels, deadline semantics, and
closed transports/tasks. Patch the SDK's clock seam for deterministic
boundary cases rather than changing the event loop's scheduler clock.

## TypeScript: Jest and built consumers

```bash
cd sdk-typescript
npm install
npm run typecheck
npm test -- --runInBand
npm run build
# probe.mjs imports the built entry point by its absolute file URL.
node "$REVIEW_DIR/probe.mjs"
# probe.cjs uses require() against the built CJS entry point.
node "$REVIEW_DIR/probe.cjs"
```

Jest is the existing test runner; target a test with
`npm test -- --runInBand --runTestsByPath <test-file>`. A TypeScript ad-hoc
driver may use `tsx` if already installed in the scratch tooling environment;
otherwise use Node against the built package without adding a repository
dependency. A source-only import cannot prove package exports work.

Use a Node HTTP server to record requests and emit controlled responses.
Exercise ESM and CJS together for error identity and root/versioned entry
points. For browser claims, run the repository's relevant bundle smoke
checks and verify no unpromised Node globals are required.

## Go CLI: commands against an HTTP peer

```bash
cd cli
go vet ./...
go test ./...
go build ./...
go run . --help
# Limit to the affected package/test once identified from the command path:
go test ./internal/... -run '<test-pattern>' -count=1
```

Write a scratch `*_test.go` beside the affected package in its disposable
copy. Start `httptest.NewServer`, record method/path/query/body/headers, and
invoke the real command with the documented API URL option. Use the command
constructor for unit-level tests and a built binary or `go run .` for exit
code and non-TTY behavior. Do not assume a failed `go run` preserves the
child's numeric exit code: use a built binary for exact status assertions.

Use `t.TempDir()` for config and downloads, `t.Setenv()` for environment,
and test-context cancellation for stalled requests. Assert JSON stdout is
parseable, diagnostics stay on stderr, credentials are redacted, and failure
status is nonzero. Compare SDK defaults only where behavior is shared.

## Budgets, streams, and contracts: shared fixture matrix

- Peer modes: instant nonterminal response; terminal on a chosen request;
  delayed exact-budget response; beyond-budget response; never responds.
  Budgets: zero, omitted, smallest accepted positive, normal, and invalid
  non-finite values. Derive expected zero semantics from the documented API.
- Record total requests and elapsed time; distinguish a returned timeout
  from a cancellation/interrupt request. Run conforming peers after each
  limiter test so false positives are visible.
- Streams: LF/CRLF/CR-only, comments, multiple `data:` lines, split UTF-8,
  empty chunks, replay cursor/reconnect, premature close, terminal failure,
  and cancellation. Compare meaningful events and output, not chunk sizes.
- Request cases: optional omitted versus null; reserved characters in path
  components; distinct source/destination endpoints; unknown response
  fields and enum values; every changed error source/code/extras combination.
- Assert a public list makes exactly one page request; an explicit iterator
  handles empty pages and cursor cycles without losing filters.
- Compare captured requests with `contract/` and run route/error drift tests
  in both SDKs. Generated-type checks require the tools documented by
  `scripts/gen-contract-types.sh`; missing tools are a recorded blocker.

Report actual measurements, for example `requests=3, result=typed timeout,
cancel requests=1`, plus the exact command. Wall-clock tolerances must be
explicit; prefer controlled clocks/handshakes in permanent regression tests.

## Packing: test what consumers install

Use an absolute `REVIEW_DIR` outside the source tree. Build from the scratch
repository; install into **another** clean consumer directory with no source
path injection. Inspect artifact contents as well as imports.

```bash
# From the scratch repository root:
(cd sdk-typescript && npm run build && npm pack --pack-destination "$REVIEW_DIR")
(cd sdk-python && uv pip install build && .venv/bin/python -m build --outdir "$REVIEW_DIR")
```

For npm, install the resulting tarball into a fresh consumer and import
`@daytona/sdk`, `@daytona/sdk/v2`, and the documented v0 surface through both
`import` and `require`; compile minimal consumers against the shipped
declarations. Exercise optional dependencies both absent and present when
relevant. Inspect `files`, loaders, ESM/CJS package scopes, and license data.

For Python, create a fresh `uv venv` in the consumer, install the exact wheel
with `uv pip install --python <consumer-python> <wheel>`, and run root and
versioned imports outside the repository. Inspect `py.typed`, generated
models, package metadata, and wheel/sdist contents. Build from the sdist in
a separate directory too when build inputs changed; a wheel built directly
from source can hide missing sdist files.

An unbundled source checkout may intentionally provide a v0 stub. Read root
`AGENTS.md` and the public release scripts before judging it. For claims
about published v0 behavior, run `scripts/bundle-v0.sh` in the scratch copy
using the committed public pin, then rebuild and test the artifact. If the
bundle cannot be obtained, report that release coverage as blocked; never
treat stub-only packing as proof of a complete published package. Do not
publish packages or alter tracked placeholder versions during review.
