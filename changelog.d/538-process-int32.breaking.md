**Process ids and exit codes are 32-bit** — a process record's `pid` and
`exitCode`, and a wait result's `exitCode`, must be signed 32-bit integers in
both SDKs, as the contract types them: one outside that range raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field.
