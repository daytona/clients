**CLI: interrupt remote exec processes** — cancelling `sandbox exec` with
Ctrl-C or SIGTERM now forwards INT, with server-side escalation to KILL after
one second, best effort. A client `--timeout` still leaves the process running.
