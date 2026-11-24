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

## Common tasks

**Run code.** `sandbox.Exec` runs a shell command, `sandbox.ExecArgs` an argv
without a shell, and `sandbox.RunCode` runs Python in a stateful interpreter.
A non-zero `ExitCode` is not an error; `err` means the call itself failed.

```go
res, err := sandbox.RunCode(ctx, "x = 21\nprint(x * 2)", nil)
```

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

var apiErr *daytona.Error
if errors.As(err, &apiErr) {
	fmt.Println(apiErr.Code, apiErr.StatusCode, apiErr.Message)
}
```

## Durations and waits

Every duration is a `time.Duration`:

- Sandbox create, fork, lifecycle operations and `Sandbox.Wait` wait up to
  60 seconds by default. `daytona.NoWait` skips waiting where supported;
  `daytona.WaitForever` removes the SDK's wait deadline. A wait that returns
  leaves the sandbox in a requested state: when the read after a target event
  shows another state, a terminal state fails the wait and any other state
  keeps it going under the same deadline. `Start` on a sandbox that is
  already starting (another caller started or resumed it) joins that
  operation and returns `*SandboxTerminalStateError` when it fails and the
  sandbox returns to stopped or paused. `Resume` of a paused macOS sandbox
  whose Mac reservation is ending returns `ErrNoActiveReservation`.
- `Images.BuildAndWait` has no readiness deadline by default. Its `Timeout`
  starts after build submission and the initial lookup, excluding local
  archiving and upload. `Images.Pull` also has no deadline by default; its
  `Timeout` covers submission, lookup and readiness together.
- Ordinary requests time out after 60 seconds by default
  (`daytona.WithRequestTimeout`); transfers use that as an idle timeout.

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
