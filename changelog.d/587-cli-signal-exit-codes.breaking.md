**CLI: shell-style exit codes for signals** — a sandbox process ended by a
signal now exits `128` + the signal's number instead of `1` for these signals,
whose numbers are the same on Linux and macOS: `HUP` (129), `INT` (130),
`QUIT` (131), `ILL` (132), `TRAP` (133), `ABRT` (134), `FPE` (136), `KILL`
(137), `SEGV` (139), `PIPE` (141), `ALRM` (142), `TERM` (143), `XCPU` (152),
`XFSZ` (153), `VTALRM` (154), `PROF` (155) and `WINCH` (156). Any other signal
(`BUS`, `USR1`, `USR2`, `STOP` and the rest) still exits `1`.
