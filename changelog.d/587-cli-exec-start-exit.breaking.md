**CLI: report command start failures like a shell** — `sandbox exec`, with
or without `--tty`, exits 127 with source `PROCESS` when a command cannot be
started, retaining the server's `PROCESS_START_FAILED` code.
