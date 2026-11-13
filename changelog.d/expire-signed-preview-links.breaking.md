**Signed preview links** — `ports.expire_signed_preview_links()` /
`ports.expireSignedPreviewLinks()` replace
`expire_signed_preview_link(port, token)` /
`expireSignedPreviewLink(port, token)`, which are removed. They take no
arguments and expire every signed preview link of the sandbox, on every port;
links created afterwards work.
