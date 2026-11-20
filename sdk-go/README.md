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
	ctx := context.Background()

	client, err := daytona.New() // reads DAYTONA_API_KEY
	if err != nil {
		log.Fatal(err)
	}
	defer client.Close()

	// An empty image boots the platform default image (Python 3 included).
	sandbox, err := client.Create(ctx, "", nil)
	if err != nil {
		log.Fatal(err)
	}
	defer sandbox.Delete(ctx, nil)

	result, err := sandbox.Exec(ctx, `python3 -c "print(6 * 7)"`, nil)
	if err != nil {
		log.Fatal(err) // the call failed; a non-zero exit code is not an error
	}
	fmt.Println(*result.ExitCode, result.Stdout)
}
```

Every call that talks to Daytona takes a `context.Context` first; cancelling it
stops the call, including waits. Optional settings are an options struct
pointer last; `nil` means all defaults.

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
endpoint, err := sandbox.Ports().Expose(ctx, 8080, &daytona.ExposePortOptions{Public: true})
fmt.Println(endpoint.URL)
```

`Ports().PreviewLink` and `Ports().SignedPreviewLink` return private links that
carry a token instead.

**Images.** A sandbox starts from an image of your project. Import a registry
image once with `Images().Pull`, or build one with an `ImageBuilder`, then
create sandboxes from its name or ID:

```go
image, err := client.Images().BuildAndWait(ctx,
	daytona.NewImageBuilder("python:3.12-slim").PipInstall("pandas"),
	&daytona.BuildImageWaitOptions{BuildImageOptions: daytona.BuildImageOptions{Name: "py-pandas"}})
sandbox, err := client.Create(ctx, image.ID, nil)
// ...
err = client.Images().Delete(ctx, image.ID) // after deleting its sandboxes
```

## Errors

Every error the SDK returns is, or wraps, a `*daytona.Error`. Match a category
or a specific code with `errors.Is`, and read details with `errors.As`:

```go
if errors.Is(err, daytona.ErrNotFound) { /* any 404 */ }
if errors.Is(err, daytona.ErrSandboxNotRunning) { /* one specific code */ }

var apiErr *daytona.Error
if errors.As(err, &apiErr) {
	fmt.Println(apiErr.Code, apiErr.StatusCode, apiErr.Message)
}
```

## Durations and waits

Every duration is a `time.Duration`. Operations that wait (create, start, stop,
image builds) wait 60 seconds by default; `daytona.NoWait` returns as soon as
the server accepted the request and `daytona.WaitForever` removes the SDK's
deadline (the context still applies). Requests time out after 60 seconds by
default (`daytona.WithRequestTimeout`).

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
