# Daytona Go SDK — development

Contributor notes for `go.daytona.com/sdk/v2`. User-facing documentation is
[`README.md`](README.md) and the package docs (`go doc`); keep internal details
out of both (see [`../AGENTS.md`](../AGENTS.md)).

## Module, path and versions

- The module path is `go.daytona.com/sdk/v2`; the package name is `daytona`.
  The code lives in `sdk-go/` of daytona/clients.
- `go.daytona.com/sdk` is served by `../scripts/go-vanity-site.sh` with the
  `go-import` subdirectory field (Go 1.25+), which maps the path to `sdk-go/`.
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
  values, pointers only where absent and zero differ.
- **Handles** (`*Sandbox`, `*ProcessHandle`, ...) are safe for concurrent use
  and expose data through methods (`ID()`, `State()`, `Info()`); `Refresh(ctx)`
  re-reads it.
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

## Commands

```sh
go build ./... && go vet ./...
go test -race -shuffle=on -count=1 ./...      # unit tests, no network
golangci-lint run ./...
./gen-contract.sh                               # after a contract change
DAYTONA_API_KEY=... go test -tags e2e -count=1 ./e2e/...
```

## Releases

The SDK publish workflow releases the module with the other SDKs: it pushes
`sdk-go/vX.Y.Z` on the commit it publishes and asks the Go module proxy to
fetch it. The tag is the version: there is no version file and no follow-up
commit (`debug.ReadBuildInfo` reports the version for the `User-Agent`). Once
the proxy has a version it cannot be deleted, only retracted by a later one.
