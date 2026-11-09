**Interpreter session responses are checked** — the interpreter session
responses are checked for the fields the SDKs read, in both SDKs: a missing or
non-string `kernel` is a `BadRequestError` (400, `BAD_REQUEST`, source `SDK`)
naming the field, and so is a missing or empty `id`, whose message names the
context id the caller chose, since that context may have been created.
