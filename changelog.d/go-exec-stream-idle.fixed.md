**Go runs with output writers survive a silently dropped stream** — `Exec`,
`ExecArgs`, `Run`, `RunArgs` and `Session.Run` with `Stdout` or `Stderr` set
follow the output on one log stream. A stream that delivers nothing for 45 seconds is now
treated as dropped and resumed after its last event, so a network path that
drops quiet connections no longer holds the run until its `Timeout` (or
forever with `WaitForever`). When the `Timeout` ends, the run checks the
process and reports how it ended instead of `timed_out` if it already exited.
