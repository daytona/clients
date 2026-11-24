**`fs.move()` timeout** — every `fs.move()` call takes a per-call timeout
that replaces the client's request timeout for that move (a move to another
file system copies in the sandbox and can outlast it). Python
`move(source, destination, timeout=...)` (sync and async) and TypeScript
`move(source, destination, { timeoutSeconds })` take seconds, where `0`, or
a value above about 24.8 days, means no limit. Go takes
`Move(ctx, source, destination, &daytona.MoveOptions{Timeout: ...})`, where
zero keeps the request timeout and `daytona.NoTimeout` means no limit; the
options are a variadic last parameter, so existing calls compile unchanged,
but a method value or an interface typed with the old
`Move(context.Context, string, string) error` signature must be updated. A
move that timed out or failed is not retried; a timeout ends the call, not
the move, so check the destination before moving again.
