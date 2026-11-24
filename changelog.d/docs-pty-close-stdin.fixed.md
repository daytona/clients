**PTY end of input** — `close_stdin()` / `closeStdin()` / `CloseStdin` are documented as exec-only: a PTY refuses them, and writing `"\x04"` (Ctrl-D) ends its input in canonical mode.
