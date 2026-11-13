**`daytona sandbox pause` reports a failed Linux pause at once** — a Linux sandbox
whose pause fails now returns to `running` with the error `the sandbox could not
be paused`, or ends `stopped` with that error when its processes could not be
kept. `sandbox pause` and `sandbox wait --for paused` exit with a conflict on
either outcome instead of waiting for `--timeout`; a `running` or `stopped`
answer without that error still keeps the wait going.
