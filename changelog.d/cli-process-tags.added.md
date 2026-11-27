**CLI: process tags and names** — `sandbox exec --tag` and `--name` start a
tagged or named process, and `sandbox process list --tag` lists the tagged
processes. `sandbox process kill-all <sandbox> --tag T [--timeout D]` kills
every process started with the tag, and everything they started, waiting up
to `--timeout` (10 seconds by default) for them to exit; it then prints the
ids of the tagged processes that were still running (processes whose records
expired are killed but not listed). A kill that does not finish within
`--timeout` fails with `PROCESS_KILL_INCOMPLETE` and prints no list: run it
again. It needs process containment: macOS and Linux guests without it
answer `UNSUPPORTED_OPERATION`. A tag or name that is not 1-128 characters
of `[A-Za-z0-9._-]` (or is `.` / `..`) is a usage error before any request.
