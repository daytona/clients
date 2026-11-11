**CLI: only a normal close ends an attach normally** — `process attach` and
`exec --tty` end as a finished session, and report the process's exit, only
when the sandbox closes the connection normally (WebSocket close `1000`, or no
status); a close for another reason (for example a policy close for an expired
replay position or a client too slow to keep up) now fails with that reason
(exit `1`) instead of waiting for the process as if the output were complete.
