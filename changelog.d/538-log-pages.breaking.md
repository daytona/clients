**Strict process log pages** — `logs()` returns the API's page as it is:
`LogPage.next_cursor` / `nextCursor` and `truncated_head` / `truncatedHead`
are required, and a new required `eof` says the process has ended and no frame
follows. A page missing one of them, or of the wrong type, raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) instead of being filled
in (Python invented a cursor from the last frame); a bare frame array is no
longer read as a page. `InterpreterProcess.process_id` / `processId` is
optional: absent, not `""`, when the run timed out while queued and was never
submitted.
