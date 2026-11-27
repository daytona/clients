**CLI: `sandbox process kill-all`** — `kill-all <sandbox> --tag T
[--timeout D]` kills every process started with the tag, and everything they
started, waiting up to `--timeout` (10 seconds by default) for them to exit;
it then prints the ids of the tagged processes that were still running
(processes whose records expired are killed but not listed). A kill that
does not finish within `--timeout` fails with `PROCESS_KILL_INCOMPLETE` and
prints no list: run it again. It needs process containment: macOS and Linux
guests without it answer `UNSUPPORTED_OPERATION`.
