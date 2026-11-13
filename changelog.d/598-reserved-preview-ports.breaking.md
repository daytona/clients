**Reserved preview ports refused locally** — the sandbox daemon's ports 2280,
2281 and 22220 are never preview ports: `ports.expose()`, `preview_link()` /
`previewLink()`, `signed_preview_link()` / `signedPreviewLink()` and the
`ports` of `create_access_token()` / `createAccessToken()` refuse them with
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) before any request, as
the API does, and `daytona sandbox port preview-url` refuses them as a usage
error. Unexposing such a port still works.
