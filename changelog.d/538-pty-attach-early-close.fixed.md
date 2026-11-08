TypeScript `attach()` on a PTY handle rejects with `ConnectionError` when the
WebSocket closes or fails before it opens, instead of hanging.
