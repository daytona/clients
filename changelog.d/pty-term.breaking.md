**PTYs get `TERM=xterm-256color`** — `pty.start()` / `pty.attach()` send
`TERM=xterm-256color` in the PTY's environment unless `env` sets `TERM`; the
sandbox's own default was `linux`, which renders fewer colors and different
line-drawing in terminal UIs.
