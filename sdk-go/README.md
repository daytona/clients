# Daytona Go SDK

The Go SDK for [Daytona](https://www.daytona.com) sandboxes: create sandboxes,
run commands and code in them, move files, use git, expose ports, and build
reusable images.

```sh
go get go.daytona.com/sdk/v2
```

```go
import "go.daytona.com/sdk/v2" // package daytona
```

Go 1.26 or newer. Full API reference: `go doc go.daytona.com/sdk/v2` or
[pkg.go.dev](https://pkg.go.dev/go.daytona.com/sdk/v2).

## Quickstart

Set `DAYTONA_API_KEY`, then:

```go
package main

import (
	"context"
	"fmt"
	"log"

	"go.daytona.com/sdk/v2"
)

func main() {
	if err := run(context.Background()); err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context) error {
	client, err := daytona.New() // reads DAYTONA_API_KEY
	if err != nil {
		return err
	}
	defer client.Close()

	// An empty image boots the platform default image (Python 3 included).
	sandbox, err := client.Create(ctx, "", nil)
	if sandbox != nil {
		defer sandbox.Delete(context.WithoutCancel(ctx), nil)
	}
	if err != nil {
		return err
	}

	result, err := sandbox.Exec(ctx, `python3 -c "print(6 * 7)"`, nil)
	if err != nil {
		return err // the call failed; a non-zero exit code is not an error
	}
	fmt.Println(result.Reason, result.Stdout)
	return nil
}
```

Every call that talks to Daytona takes a `context.Context` first; cancelling it
stops the call, including waits. Optional settings are an options struct
pointer last; `nil` means all defaults.

Once a create or fork is accepted, its handle is returned even if the caller's
context ends the wait with an error. Delete the non-nil handle or keep it;
cleanup needs a context that has not been cancelled.

## Persistence

Linux sandboxes are non-persistent by default. Set `CreateOptions.Persistent`
to a pointer to `true` (also through `CreateFromBuildOptions.CreateOptions`) for
persistence. When neither a per-create policy nor a project lifecycle default
overrides it, persistent Linux sandboxes pause after a 300-second lifetime.

- `Stop` / `Start` preserve disk, not memory or running processes.
  `Stop` requires persistence, including on macOS, and is unavailable on GPU.
- `Pause` / `Resume` preserve disk, memory and processes.
- Linux stop, start, pause, resume and fork require persistence; the
  API checks support.
- Lifecycle policies allow `delete` everywhere, `stop` on persistent non-GPU
  sandboxes, and `pause` on persistent Linux.
- Volumes are accepted on create, but sandboxes with attached volumes currently
  reject pause and fork with `ErrConflict`.
- Snapshot restores inherit the source's persistence; there is no override.

Submissions retry only `SANDBOX_TRANSITION_IN_PROGRESS` for up to 30 seconds:
`Fork` while the source is still starting, stopping or pausing;
`CaptureImage` while it is still starting (Linux, Windows) or stopping (macOS);
and `CreateSnapshot` (memory and disk) while it is still starting.
Other conflicts are returned immediately.
The window limits new retries; an admitted POST keeps its normal request timeout.

`Search` / `SearchAll` accept `SearchSandboxesOptions.Persistent`: a pointer to
`true` selects persistent sandboxes, a pointer to `false` selects ephemeral
sandboxes, and `nil` omits the filter.

## Common tasks

**Run code.** `sandbox.Exec` runs a shell command, `sandbox.ExecArgs` an argv
without a shell, and `sandbox.RunCode` runs Python in a stateful interpreter.
A non-zero `ExitCode` is not an error; `err` means the call itself failed.

```go
res, err := sandbox.RunCode(ctx, "x = 21\nprint(x * 2)", nil)
```

The first run of a language installs its kernel when the image does not ship
it (Python's `ipykernel` from PyPI, Deno from `dl.deno.land`), which adds a few
seconds and needs those hosts reachable under a network policy. When the kernel
cannot be installed or started, `RunCode` returns `ErrKernelUnavailable` with
the sandbox's setup diagnostic in the message.

**Metadata after create.** A handle from `Create*` holds only what the create
response reports; call `sandbox.Refresh(ctx)` before reading `Info().Resources`,
labels or other detail fields.

**Files.** Paths are inside the sandbox.

```go
err = sandbox.FS().WriteFile(ctx, "/home/daytona/data.csv", []byte("1\n2\n"), nil)
data, err := sandbox.FS().ReadFile(ctx, "/home/daytona/data.csv", nil)
err = sandbox.FS().UploadFile(ctx, "local.csv", "/home/daytona/local.csv", nil)
err = sandbox.FS().DownloadFile(ctx, "/home/daytona/result.txt", "result.txt", nil)
```

`Upload` and `Download` stream from an `io.Reader` / to an `io.Writer`.

**Git.**

```go
git := sandbox.Git()
err = git.Clone(ctx, "https://github.com/octocat/Hello-World", "/home/daytona/repo", nil)
err = git.CreateBranch(ctx, "/home/daytona/repo", "feature")
err = git.CheckoutBranch(ctx, "/home/daytona/repo", "feature")
err = git.Add(ctx, "/home/daytona/repo", []string{"NOTES.md"})
_, err = git.Commit(ctx, "/home/daytona/repo", "Add notes",
	&daytona.GitCommitOptions{Author: "Ada", Email: "ada@example.com"})
history, err := git.History(ctx, "/home/daytona/repo")
```

**A public URL for a port.** Start a server in the sandbox, then expose the
port publicly; the URL opens without credentials.

```go
_, err = sandbox.Process().Start(ctx, "python3 -m http.server 8080", nil)
if err != nil {
	return err
}
endpoint, err := sandbox.Ports().Expose(ctx, 8080, &daytona.ExposePortOptions{Public: true})
if err != nil {
	return err
}
fmt.Println(endpoint.URL)
```

`Ports().PreviewLink` preserves the endpoint's visibility (public endpoints have
no token). `Ports().SignedPreviewLink` returns a token-bearing private link:

```go
link, err := sandbox.Ports().SignedPreviewLink(ctx, 8080,
	&daytona.SignedPreviewLinkOptions{ExpiresIn: 5 * time.Minute})
```

Pass `nil` options or zero `ExpiresIn` for the server's default lifetime.

**Images.** Use `CreateFromBuild` to build an anonymous image and boot a
sandbox in one call:

```go
sandbox, err := client.CreateFromBuild(ctx,
	daytona.NewImageBuilder("python:3.12-slim").PipInstall("six==1.17.0"),
	&daytona.CreateFromBuildOptions{
		CreateOptions: daytona.CreateOptions{Wait: time.Minute},
		BuildTimeout:  4 * time.Minute,
		IdleExpiry:    time.Hour,
	})
if sandbox != nil {
	defer sandbox.Delete(context.WithoutCancel(ctx), nil)
}
if err != nil {
	return err
}
// Use sandbox.Exec; sandbox.Info().Image.ID identifies the built image.
```

All create options are checked before the billed build. The shared `CPUArch` type
provides `CPUArchAMD64` and `CPUArchARM64` for both sandbox and image options.
`Region` and `CPUArch` apply to both stages. `BuildTimeout` bounds readiness
separately (zero or `WaitForever` means no SDK deadline); the sandbox then
receives its full `Wait`.
Build failures return `*ImageBuildFailedError` without creating a sandbox.
Anonymous images expire under their idle-retention policy.

To keep the image for reuse, use `Images().BuildAndWait` instead. You can also
import a registry image once with `Images().Pull`, then create from its name or ID:

```go
image, err := client.Images().BuildAndWait(ctx,
	daytona.NewImageBuilder("python:3.12-slim").PipInstall("pandas"),
	&daytona.BuildImageWaitOptions{BuildImageOptions: daytona.BuildImageOptions{Name: "py-pandas"}})
if err != nil {
	return err
}
sandbox, err := client.Create(ctx, image.ID, nil)
if sandbox != nil {
	defer sandbox.Delete(context.WithoutCancel(ctx), nil)
}
if err != nil {
	return err
}
// ...
```

Delete the image only after its sandboxes have been deleted.

## Errors

Every error an SDK call returns is, or wraps, a `*daytona.Error`. Match a
category or a specific code with `errors.Is`, and read details with `errors.As`.
Streams that implement `io.Reader` follow its contract instead:
`PTYConnection.Read` returns `io.EOF`, unwrapped, when the terminal closes
normally.

```go
if errors.Is(err, daytona.ErrNotFound) { /* any 404 */ }
if errors.Is(err, daytona.ErrSandboxNotRunning) { /* one specific code */ }
if errors.Is(err, daytona.ErrSandboxTransitionInProgress) { /* retry window exhausted */ }

var apiErr *daytona.Error
if errors.As(err, &apiErr) {
	fmt.Println(apiErr.Code, apiErr.StatusCode, apiErr.Message)
}
```

`Fork`, `CaptureImage` and `CreateSnapshot` retry only
`ErrSandboxTransitionInProgress` for up to 30 seconds from the first attempt.
The API's retry delay is clamped to 1–5 seconds; other conflicts are returned
immediately. Caller cancellation, deadlines and client closure interrupt retries.
Fork's readiness wait keeps its separate budget.

## Durations and waits

Every duration is a `time.Duration`:

- Sandbox create, fork, lifecycle operations and `Sandbox.Wait` wait up to
  60 seconds by default. `daytona.NoWait` skips waiting where supported;
  `daytona.WaitForever` removes the SDK's wait deadline. When `Sandbox.Wait`
  or a lifecycle operation returns nil, `State()` reports the target it
  observed. When the read after a target event shows another state, a state
  terminal for that wait returns `*daytona.SandboxTerminalStateError`, and
  any other state keeps polling until the deadline. `Start` on a sandbox that
  is already starting (another caller started or resumed it) joins that
  operation and returns `*daytona.SandboxTerminalStateError` when the sandbox
  returns to stopped or paused, once the wait's long-poll reads confirm it
  (up to one 30-second slice). A failed `Pause` is reported the same
  way, after up to one 30-second long-poll read. `Resume` of a paused macOS
  sandbox whose Mac reservation is ending returns
  `daytona.ErrNoActiveReservation`.
- `Images.BuildAndWait` has no readiness deadline by default. Its `Timeout`
  starts after build submission and the initial lookup, excluding local
  archiving and upload. `Images.Pull` also has no deadline by default; its
  `Timeout` covers submission, lookup and readiness together.
- A recursive remove or a search over a large tree can take longer than the
  request timeout. `FileSystem.Remove`, `Grep`, `Glob`, `Replace` and `Chmod`
  therefore take a `Timeout` option (`RemoveFileOptions`, `FileSearchOptions`,
  `ReplaceOptions`, `FilePermissionsOptions`) that replaces the request timeout
  for that call. Zero keeps the request timeout; `daytona.NoTimeout`, or a
  duration above 2,147,483.647 seconds, means no limit. Any other negative
  value returns `ErrBadRequest` before a request is sent. A timeout ends the
  call, not the work in the sandbox.
- `Exec`, `ExecArgs`, `Run`, `RunArgs` and `Session.Run` with `Stdout` or
  `Stderr` writers follow the output on a log stream. A stream that delivers nothing
  for 45 seconds is treated as dropped and resumed after the last event, so a
  network path that drops quiet connections does not hold the run until its
  `Timeout`. When the `Timeout` ends, the SDK first reads the retained log
  (up to eight pages, each an ordinary request bounded by the request
  timeout), then checks the process with one read bounded at 5 seconds; if
  that check succeeds and the process already ended, the run reports how it
  ended rather than `timed_out`.
- Ordinary requests time out after 60 seconds by default
  (`daytona.WithRequestTimeout`); transfers use that as an idle timeout.
  `FileSystem.Move` takes `&daytona.MoveOptions{Timeout: ...}` to replace it
  for one move: zero keeps the request timeout, and `daytona.NoTimeout` or a
  duration above 2,147,483.647 seconds means no limit. A move to another file
  system is a copy in the sandbox. A move that timed out or failed is not
  retried (only a 401, refused before anything moves, is re-sent once with a
  fresh token), and a timeout ends the call, not the move; check the destination
  before moving again.
- Process runs without `Stdout` / `Stderr` writers (`Exec`, `Run`, `RunArgs`,
  `Session.Run`) and `ProcessHandle.Wait` wait in rounds: the sandbox holds
  each request for at most 30 seconds (its read is bounded at 35 seconds),
  whatever the `Timeout` (or `WaitForever` / `daytona.NoTimeout`), so a
  dropped quiet connection costs one retried round, not the whole wait. A run with `Stdout` or
  `Stderr` set follows its output on one log stream for the whole run,
  bounded only by `Timeout` and the context. The request that starts a
  process is never retried.
- Waits that read the server (sandbox waits, `Docker.WaitUntilReady`,
  `Images.Get` / `Snapshots.Get` with `Wait` and a positive `Timeout`, the
  readiness waits of `Images.Pull`, `Images.BuildAndWait` and
  `CreateFromBuild`) retry a read that fails transiently (no response, a
  client read timeout, HTTP 429 other than `daytona.ErrLimitExceeded`, or a
  5xx other than 501) with backoff (200 ms doubling to 5 s, or the server's
  `RetryAfter` when longer) within the same budget, and never sooner than the
  wait's own poll spacing (200 ms for sandbox waits, 500 ms for Docker, 1 s
  for image and snapshot readiness). The timeout then wraps the last failed
  read. A wait without a deadline keeps retrying until the server answers,
  the context ends or the client is closed. `Images.Get` / `Snapshots.Get`
  with `Wait` but no `Timeout` make a single server-default hold and do not
  retry. Any other failure returns at once, with one exception: while the
  events stream is live, its early probes of a sandbox wait keep waiting
  through any failure other than 401, 403, 404 or a closed client, and the
  wait's timeout then wraps that failure. A failed probe is retried on the
  probes' own schedule (after about 10 s of socket silence), not on the read
  backoff.

The caller's context always applies, including to unbounded waits.

## Configuration

`daytona.New` reads the environment; options override it:

| Option | Environment | |
|---|---|---|
| `WithAPIKey` | `DAYTONA_API_KEY` | project API key |
| `WithJWTToken` + `WithProjectID` | `DAYTONA_JWT_TOKEN`, `DAYTONA_PROJECT_ID` | user token scoped to a project |
| — | `DAYTONA_V2_API_URL` | API endpoint (default Daytona Cloud) |
| `WithRequestTimeout`, `WithMaxResponseBytes`, `WithHTTPClient`, `WithLogger`, `WithSandboxEvents` | | transport tuning |

For tracing, pass an instrumented client: `daytona.WithHTTPClient(&http.Client{Transport: otelhttp.NewTransport(http.DefaultTransport)})`.

## Versions and the 0.x SDK

This module carries the Daytona platform version (`v2.X.Y`, the same as the
Python and TypeScript SDKs). `go get -u` never moves code between it and the
0.x Go SDK (`github.com/daytona/clients/sdk-go`), which keeps working for the
Daytona v0 API. A program can use both: import the 0.x package under an alias,
for example `daytonav0 "github.com/daytona/clients/sdk-go/pkg/daytona"`.
