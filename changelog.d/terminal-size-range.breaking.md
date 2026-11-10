**Terminal sizes are 1 to 65535** — in both SDKs, `pty.start(terminal=...)` /
`pty.start(command, { terminal })` and `handle.resize()` refuse `cols` / `rows` above 65535 with `BadRequestError`
before any request (the upper bound was the int32 maximum, which the sandbox
refused above 65535 with a 422).
