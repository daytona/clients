**`fs.move()` timeout** — `fs.move()` takes a per-call timeout that replaces
the client's request timeout for that move, for moves to another file system
that copy in the sandbox: Python `move(source, destination, timeout=...)`
(sync and async), TypeScript `move(source, destination, { timeoutSeconds })`,
Go `Move(ctx, source, destination, &daytona.MoveOptions{Timeout: ...})`. `0`
(Go `daytona.NoTimeout`) means no limit. A move is never retried; a timeout
ends the call, not the move, so check the destination before moving again.
