**Process records are checked in full** — a created process record needs a
non-empty `id`, and the record a timed-out `wait()` re-reads is checked in
full, in both SDKs (Python checked only its generated model, so an empty `id`
or a bad `createdAt` still returned a result). A violation raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field, instead
of being filled with an empty value.
