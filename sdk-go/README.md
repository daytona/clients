# Daytona Go SDK

```sh
go get go.daytona.com/sdk/v2
```

Set `DAYTONA_API_KEY`, then connect to an existing sandbox:

```go
client, err := daytona.New()
if err != nil {
    return err
}
defer client.Close()

sandbox, err := client.Get(ctx, "sandbox-id", nil)
if err != nil {
    return err
}
fmt.Println(sandbox.ID(), sandbox.State())
```

Import `go.daytona.com/sdk/v2` as `daytona`. JWT authentication uses
`WithJWTToken` and `WithProjectID`, or `DAYTONA_JWT_TOKEN` and
`DAYTONA_PROJECT_ID`. Explicit credentials override ambient credentials.
`DAYTONA_V2_API_URL` selects an endpoint for test environments; otherwise the
SDK connects to Daytona Cloud.

`List` and `Search` return one page; `ListAll` and `SearchAll` are cancellable
Go iterators. `FindByName` returns all matches. `sandbox.Info()` returns an
independent metadata snapshot; `sandbox.Refresh(ctx)` updates it.

```go
if errors.Is(err, daytona.ErrNotFound) {
    // The requested resource does not exist.
}
var detail *daytona.Error
if errors.As(err, &detail) {
    fmt.Println(detail.Code, detail.StatusCode)
}
```

Durations use `time.Duration`. The ordinary request timeout is 60 seconds
including authentication and its single refresh retry. Use
`WithRequestTimeout(2*time.Minute)` to replace it or
`WithRequestTimeout(daytona.NoTimeout)` to disable it. Context cancellation
always applies. Zero option fields keep defaults.

Buffered responses are capped at 1 GiB by default;
`WithMaxResponseBytes(daytona.NoLimit)` disables that cap.
Always close the client when finished. Handles are safe for concurrent use.
