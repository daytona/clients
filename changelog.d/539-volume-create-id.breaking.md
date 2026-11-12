**A created volume needs a usable `id`** — a `volumes.create()` response whose
`id` is empty or blank is a `BadRequestError` (400, `BAD_REQUEST`, source
`SDK`) after the one request, in both SDKs; it returned a volume that could
not be mounted or deleted.
