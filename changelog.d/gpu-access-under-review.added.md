**`GPU_ACCESS_UNDER_REVIEW` error code** — the API refuses a GPU sandbox create with this 403 code when the
organization's GPU access is under review; both SDKs list it in their code catalogs and raise the existing
`ForbiddenError` for it.
