**PTY attach on an early close** — TypeScript `attach()` on a PTY handle
rejects with `ConnectionError` when the WebSocket closes or fails before it
opens, instead of hanging (on a close) or rejecting with a plain
`DaytonaError` (on an error). The pair `pty.attach()` returns,
`{ handle, connection }`, is frozen.
