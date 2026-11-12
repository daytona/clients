**Pre-signed URL responses are checked** — the API's pre-signed file URL
response is checked against the contract in both SDKs; a violation is a
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field, where
it raised a 500 `InternalServerError`.
