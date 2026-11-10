**Terminal sizes are 1 to 65535** — `pty.start(terminal=...)` and
`handle.resize()` refuse `cols` / `rows` above 65535 with `BadRequestError`
before any request (they accepted any int32, which the sandbox then refused
with a 422).
