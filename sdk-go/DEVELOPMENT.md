# Daytona Go SDK — development

Contributor notes for `go.daytona.com/sdk/v2`. User-facing documentation is
[`README.md`](README.md) and the package docs (`go doc`); keep internal details
out of both (see [`../AGENTS.md`](../AGENTS.md)).

## Module, path and versions

- The module path is `go.daytona.com/sdk/v2`; the package name is `daytona`.
  The code lives in `sdk-go/` of daytona/clients.
- Serving `go.daytona.com/sdk` depends on the vanity-site deployment from
  [PR #688](https://github.com/daytona/clients/pull/688), which supplies
  `scripts/go-vanity-site.sh` and `.github/workflows/go-vanity-pages.yaml`.
  Its `go-import` subdirectory field (Go 1.25+) maps the path to `sdk-go/`;
  the publish preflight checks the
  deployed meta tag before creating any release tag.
- Versions are the platform versions of the other SDKs. A release `vX.Y.Z`
  also carries the tag `sdk-go/vX.Y.Z` on the same commit; Go reads only the
  prefixed tag.
- The `/v2` suffix is Go's semantic import versioning: no `go get -u` or
  `@latest` ever moves a user between `go.daytona.com/sdk/v2` and the 0.x module
  `github.com/daytona/clients/sdk-go` (released from `release/v0`, tags
  `sdk-go/v0.x.y`). **Never tag `sdk-go/v1.*`**: 0.x would read it as its own
  next version. A future major is a new path (`go.daytona.com/sdk/v3`).
- There is no v0 namespace. Go programs that still use the Daytona v0 API
  import the 0.x module next to this one; both build into one binary.

## Layout

| Path | What it is |
|---|---|
| `*.go` (package `daytona`) | The whole public API: `New`, options, `Client`, `Sandbox` and its services, models, errors and codes. Unexported helpers live next to the code that uses them. |
| `internal/gen/api`, `internal/gen/toolbox` | Wire models generated from `../contract/` by `./gen-contract.sh`. Never part of the public API. |
| `internal/...` | Self-contained machinery with its own tests (protocol clients, archive and ignore-file handling). |
| `example_test.go`, `*_example_test.go` | Runnable examples, shown by `go doc` and pkg.go.dev. |
| `e2e/` | Live tests against a Daytona deployment (build tag `e2e`). |

Files stay under 250 lines of code (blank and comment lines excluded); split by
responsibility, one type and its methods per file where possible.

`./gen-contract.sh` regenerates the wire models;
`../scripts/gen-contract-types.sh --check` fails CI when they are stale.

## API conventions

The SDK implements the same 2.x contract as the Python and TypeScript SDKs
(operations, wire behaviour, errors, wait semantics; see
[`../docs/VERSIONING.md`](../docs/VERSIONING.md)). Where the other SDKs differ
only because of their language (option bags, `Seconds` in names, async), Go
does it the Go way:

- **`context.Context` first** on every method that does I/O. Cancelling it stops
  the call, including waits; a context deadline bounds waits too.
- **Required arguments positional, optional ones in an options struct pointer
  last**: `Create(ctx, image string, opts *CreateOptions)`. `nil` means all
  defaults; a zero field means that field's default.
- **`time.Duration` for every duration** (timeouts, wait budgets, TTLs,
  intervals). Convert at the wire boundary with the same rounding as the other
  SDKs. Special values: `daytona.NoWait` (return once the server accepted the
  request), `daytona.WaitForever` (no SDK deadline; the context still applies),
  `daytona.NoTimeout` (no request or idle timeout).
- **`time.Time` for timestamps.**
- **Units in names when not bytes**: `MemoryGiB`, `DiskGiB`, `CPU` (cores).
  Byte counts are `int64`; `daytona.NoLimit` disables a size cap.
- **Enums are string types with constants.** Unknown server values are kept:
  the typed field holds the `...Unknown` constant and a `Raw...` field the
  server's string.
- **Models are plain structs** the caller owns; absent optional fields are zero
  values, pointers only where absent and zero differ. An optional response
  string whose absence means the same as `""` (no reason, no error, no name) is
  a plain `string`, not a `*string`.
- **Returns**: a method that reads or creates one object returns a pointer
  (`*Image`, `*DockerStatus`, `*FileInfo`); pages and iterators hold values.
  Identity snapshots (`ProcessHandle.ToJSON`, `Session.ToJSON`,
  `InterpreterContext.Identity`) are small values that round-trip through the
  matching `Connect...` method by value.
  `Sandbox.ToJSON` returns the shared credential- and URL-free
  `SandboxHandleJSON` metadata snapshot (GiB capacity, seconds for lifecycle
  budgets, verbatim wire timestamps); resume with `client.Get(ctx, json.SandboxID, nil)`.
- **Handles** (`*Sandbox`, `*ProcessHandle`, `*Session`, ...) are safe for
  concurrent use and expose data through methods. `*Sandbox` keeps its last
  observation (`ID()`, `State()`, `Info()`) and `Refresh(ctx)` re-reads it;
  `*ProcessHandle` keeps only its identity (`ID()`, `Kind()`) and reads the
  process with `Get(ctx)`.
- **Lists**: `List(ctx, opts) (*Page[T], error)` returns one page;
  `ListAll(ctx, opts) iter.Seq2[T, error]` pages through all of them.
- **Streams**: byte output goes to `io.Writer` fields (`Stdout`, `Stderr`);
  transfers take `io.Reader` / `io.Writer`; event streams are `iter.Seq2`.
- **Errors**: everything the SDK returns is (or wraps) a `*daytona.Error`
  (`StatusCode`, `Code`, `Source`, `Message`, cause via `Unwrap`):
  - `errors.Is(err, daytona.ErrSandboxNotRunning)` matches an error code;
  - `errors.Is(err, daytona.ErrNotFound)` matches the category of the error's
    kind (the Python/TypeScript class hierarchy);
  - errors with extra data are their own types (for example
    `*daytona.SandboxTerminalStateError`) and unwrap to their `*daytona.Error`;
  - invalid input is a `BAD_REQUEST` error with source `SDK`, returned before
    any request; the SDK never panics on caller input;
  - `context.Canceled` / `context.DeadlineExceeded` stay reachable.
- **No global state** besides the once-per-process `DAYTONA_API_URL` warning.
  Background goroutines end with `Client.Close` or their context. Logging goes
  through `log/slog` (`daytona.WithLogger`).
- **Tracing**: wrap the transport of `daytona.WithHTTPClient` (for example with
  `otelhttp.NewTransport`); the SDK has no tracing dependency of its own.
- **Doc comments on every exported identifier**, written for a reader who only
  has `go doc`, plus runnable examples for common tasks.

### Deliberate Go-only additions

- `Images.BuildAndWait` composes image submission and readiness waiting.
  Unbounded readiness waits in `Pull` and `BuildAndWait` still cap each server
  hold at 60 seconds and each read at 65 seconds, matching Go's artifact waiter.
  TypeScript's unbounded `pull` currently leaves individual holds unbounded.
- `ExecArgs` / `StartArgs` accept argument vectors without shell quoting.
- Transfers accept `io.Reader` / `io.Writer` for caller-owned byte streams.
- `Images.BuildFromContextReader` builds from a caller-supplied archive stream.
- `ListAll` iterators expose lazy traversal separately from single-page lists.

## Commands

```sh
go build ./... && go vet ./...
go test -race -shuffle=on -count=1 ./...      # unit tests, no network
golangci-lint run ./...
./gen-contract.sh                               # after a contract change
DAYTONA_API_KEY=... go test -tags e2e -count=1 ./e2e/...
```

From the repository root, `./e2e/run.sh go` runs the same live suite the way CI's
`e2e.yaml` does, and `./e2e/run.sh all` runs it with the Python and TypeScript
suites. `SMOKE_IMAGE`, `DAYTONA_SKIP_TOOLBOX` and `DAYTONA_SKIP_DOCKERFILE`
narrow it for deployments without those features.

## Releases

The SDK publish workflow releases the module with the other SDKs: it pushes
`sdk-go/vX.Y.Z` on the commit it publishes and asks the Go module proxy to
fetch it. The tag is the version: there is no version file and no follow-up
commit (`debug.ReadBuildInfo` reports the version for the `User-Agent`). Once
the proxy has a version it cannot be deleted, only retracted by a later one.

## Internal transport API

All area implementations live in package `daytona`; do not create a second HTTP
client, auth cache, response reader, or error hierarchy.

```go
var response wireResponse // an internal wire struct or JSONValue
err := c.api().do(ctx, apiRequest{
    Method: http.MethodPost, Path: "/sandboxes/" + segment + "/operation",
    Body: wireRequest, Result: &response,
    Query: url.Values{"wait_seconds": {"30"}},
    Headers: http.Header{"If-Match": {revision}},
    IdempotencyKey: key,
})
err = s.toolbox().do(ctx, toolboxRequest{Path: "/files", Result: &response})
```

- `apiRequest` and `toolboxRequest` share a description. Zero `Method` is GET;
  `Path` must be relative. Use `pathSegment(id)` for every path identifier.
  JSON `Body`/`Result` are encoding boundaries, never domain `any` values.
  Non-empty responses declared exactly `application/json` are checked even
  with no `Result`. With no `Result`, empty or non-JSON success bodies are
  accepted without JSON validation; a `Result` requires a non-empty JSON body.
- `Timeout` replaces the whole request budget, including auth, refresh and the
  sole 401 retry. Zero uses the client default; `NoTimeout` disables it.
  Waits/long polls must supply their own budget or `NoTimeout`.
- `MaxResponseBytes` overrides the buffered-body cap; zero uses the client
  default and `NoLimit` disables it. `ResponseBody` receives buffered raw bytes
  when needed, and `ResponseHeaders` receives a cloned header map.
- `stream(ctx, request) (*http.Response, error)` is available on both transports.
  The caller **must close `response.Body`**. Its total timeout stops at response
  headers; caller cancellation and `Client.Close` still interrupt the body.
  Set `Transfer: true` for uploads/downloads: the request timeout becomes an
  idle bound across authentication, connecting, upload, headers, and body reads.
  Streaming bodies have no total size cap.
- `BodyReader` is a one-shot upload. It is never replayed after 401.
  `BodyFactory func(context.Context) (io.ReadCloser, error)` supplies a fresh body
  for each attempt when retry is safe. Readers must unblock on cancellation;
  the HTTP transport closes acquired bodies. Prefer a context-aware closer for
  a reader backed by a potentially blocking producer.
- `s.toolbox().connectionInfo(ctx)` returns the current base URL plus identity
  and Authorization headers for a future WebSocket dialer. Dialers must preserve
  context cancellation and refuse redirects. It does not implement WebSockets.
- `newSDKError(code, status, message)`, `badRequest(message)`,
  `wrapError(cause, message)`, `transportError(ctx, cause)` and
  `errorFromResponse(response, bytes)` keep failures in the shared hierarchy.
  Extra-data errors carry `SDKError *Error` and unwrap to it. Docker status
  types needed by those errors are already defined in `errors_docker.go`.
- `field[T]`, `integerField`, `timestampField` and `parseObject` are boundary
  parsing helpers. Generated wire models may be used internally, never publicly.
  Sandbox parsing deliberately checks only fields the SDK reads: generated
  int32/UUID constraints would reject values the TypeScript boundary accepts.
  Integer response fields accept JSON `3.0` as `3`, like TypeScript; Python refuses it.
- `c.newSandbox(sandboxInit{Info: info, ToolboxURL: proxyBase})` constructs a
  handle for create/fork workers. `ToolboxURL` is an API-trusted **unscoped base**.
  `Info` is copied; `Options.ToolboxURL` is a trusted direct override. For
  API-issued initial access tokens set `FromCreate: true`, `AccessToken`
  and `AccessTokenExpiresAt` on `sandboxInit` (never on `SandboxInfo`, which
  must stay printable without leaking a credential); caller tokens are never automatically minted
  over. `Refresh` updates observations but preserves a direct URL override.
- `checkDuration(name, value, allowed...)` validates a caller duration option:
  zero (the default) and positive values pass, each sentinel listed in
  `allowed` (`NoWait`, `WaitForever`, `NoTimeout`) passes, any other negative
  value is a `BAD_REQUEST` from the SDK naming the option. Call it for every
  `time.Duration` a caller passes, before any request.
- `*Error` keeps the identification fields; per-code server data lives in
  `Error.Details` (`ErrorDetails`). `Error()` prints
  `daytona: <message> (<code>, HTTP <status>)`; `Message` is the explanation
  alone. JSON-envelope messages remain verbatim; non-JSON fallback text has
  surrounding whitespace trimmed. A wrapped local failure includes its cause's
  text and preserves the cause for `errors.Is` and `errors.As`.
- `wireSeconds` and `wireMilliseconds` round positive durations upward;
  `boundedTimeout` treats a budget above 2,147,483.647 seconds as unbounded.
  Zero is the public option default; `NoWait`, `WaitForever`, `NoTimeout` and
  `NoLimit` are explicit, distinct sentinels.
- `paginate(ctx, initialCursor, fetch)` adapts a
  `func(context.Context, string) (*Page[T], error)` into `iter.Seq2[T, error]`.
  It stops on a repeated cursor and never fetches after the consumer breaks.
- `Client.stateSource` is a `sandboxStateSource`. Its
  `watch(ctx, sandboxID, region)` returns the observation channel, an
  idempotent release and per-watch `events.WatchLiveness`. A nil channel means
  unavailable: use API reads. The producer owns closing the channel.
  Liveness includes registration time and a lock-protected snapshot of the
  current socket's last incoming frame, following routing changes. Ping/pong,
  errors and frames for other IDs count even without a state transition.
  Early probes follow `client_rules.early_probe` in the events contract:
  first at `jittered(min(10s, hub budget / 2))`, then only after 10s of silence
  measured from the later of registration and the last frame. The first probe
  for a short budget instead requires no frame since registration. Jitter is
  +/-10%, using the hub's injectable random source. A target or terminal read
  settles directly, even when a frame or socket change overlaps its GET.
  Only a nonterminal successful read is discarded on overlap, without adopting
  its metadata. A nonterminal read rearms for the remaining quiet
  time (at least 200ms), a failed probe for a fresh jittered 10s. Reads that
  fail with 401/403/404 stop the wait. The final reserve and events-disabled
  polling do not depend on liveness.
- Tests share `newFakeServer`, `writeJSON`, `jsonHandler`, `sandboxFixture` and
  `newTestClient`; see the usage comment at the top of `helpers_test.go`.
  The client helper uses `t.Setenv`, so its tests must not call `t.Parallel`.
- `go run codes_generate.go` refreshes the code constants/sentinels from the
  sibling SDK's contract-checked catalog. A drift test checks every source/code.
  Regenerate after `sdk-typescript/src/v2/codes.ts` changes.
- Drift and shared-fixture tests read files outside `sdk-go/` (`contract/`, the
  TypeScript catalog and route list, the Python and CLI fixtures). They skip
  when a file is absent, because the published module zip holds only `sdk-go/`.
  The repository's CI sets `DAYTONA_SDK_GO_REQUIRE_FIXTURES=1`, which turns a
  missing file into a failure, so a rename cannot silently disable a guard.
