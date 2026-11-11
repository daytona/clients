**CLI: `sandbox exec` creates within its `--timeout`** — starting the process
uses the remaining `--timeout` (at most 30 seconds) and exits 8 when it runs
out, while Ctrl-C during the start still interrupts the process once it exists.
