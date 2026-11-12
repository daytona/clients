**macOS fork completion** — Python `fork(wait_seconds=…)`, TypeScript
`fork({ waitSeconds })`, and `daytona sandbox fork` wait for `started`
(`running` in the CLI) when the source is running, passing through an
intermediate stopped state. Forks of paused or stopped sources wait for
`stopped`. Both SDKs default to a 60-second wait budget and return the handle
if it expires before the fork reaches that state; check `fork.state` before
using it, and call `fork.wait()` to continue waiting.
