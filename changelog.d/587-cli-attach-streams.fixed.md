**CLI: attach and tty streams** — `process attach` and `exec --tty` send the
current terminal size when they attach, a dropped connection is an error rather
than a finished stream, and interrupting a handshake or a picker exits `130`
(`143` on SIGTERM).
