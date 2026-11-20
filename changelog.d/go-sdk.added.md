**Go SDK** — `go.daytona.com/sdk/v2` (package `daytona`) brings the 2.x SDK to
Go with the same operations, errors and wait behaviour as Python and
TypeScript: sandboxes and their lifecycle, commands, code, PTYs and sessions,
files, git, ports and preview links, images and the image builder, snapshots,
volumes, secrets and computer use. It takes a `context.Context` on every call
and `time.Duration` for every duration, and is released with every SDK release
as the tag `sdk-go/vX.Y.Z`. The 0.x Go SDK (`github.com/daytona/clients/sdk-go`)
is unchanged; `go get -u` never moves code between the two.
