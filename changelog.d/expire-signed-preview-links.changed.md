**Signed preview links** — expiring a link (`expire_signed_preview_link` /
`expireSignedPreviewLink`, `daytona sandbox port preview-url expire`) now
revokes every signed preview link on the sandbox, on every port, when the
token is a live link for that sandbox and port; any other token still changes
nothing and raises no error. Tokens are opaque: do not rely on their length or
alphabet.
