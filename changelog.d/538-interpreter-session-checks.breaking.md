**Interpreter session responses are checked** — the interpreter session
responses are checked for the fields the SDKs read (`id`, `kernel`), in both
SDKs; a missing or empty `id` is a `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`) naming a context id the caller chose, which may have been
created.
