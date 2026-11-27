**`search` is reserved for secret and volume search** — the SDKs and the CLI
now reject creating, looking up, or deleting a secret or volume named exactly
`search` before sending a request, because those paths identify the search
routes. In Go, `Secrets().Get`, `Set` and `Delete` and `Volumes().Get`,
`Create` and `Delete` return `ErrBadRequest`. `SEARCH` stays valid.
