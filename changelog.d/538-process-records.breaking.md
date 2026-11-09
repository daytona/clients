**Checked process records** — the record `start()`, `run()`, `exec()` and
`create_session()` / `createSession()` get back from the sandbox is checked in
full in both SDKs (TypeScript checked only its `id`, so a record without
`state` read as running); a record that breaks the contract raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field.
