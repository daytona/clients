**Signed preview links: `expire_signed_preview_links()` replaces `expire_signed_preview_link(port, token)`** —
`ports.expire_signed_preview_links()` / `ports.expireSignedPreviewLinks()`
take no arguments and expire every signed preview link of the sandbox, on
every port; links created afterwards work. `expire_signed_preview_link()` /
`expireSignedPreviewLink()` are removed. In the CLI, `daytona sandbox port
preview-url expire <sandbox-id>` takes no port or token, and its JSON is
`{"sandbox_id", "revoked": true}`.
