**PTY end of input** — `close_stdin()` / `closeStdin()` / `CloseStdin` are
documented as exec-only: on a PTY the sandbox answers 400, and writing
`"\x04"` (Ctrl-D) ends its input in the terminal's default canonical mode, at
the start of a line.
