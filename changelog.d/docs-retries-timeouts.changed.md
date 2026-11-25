**Retries and timeouts** — the Errors sections say which paths retry on their
own (the 401 refresh, build-context uploads, process waits, log followers and
the events stream), that a bare `Retry-After` header does not set
`retry_after_seconds`, and that a client timeout does not cancel remote work;
the interpreter timeout documents its interrupt grace.
