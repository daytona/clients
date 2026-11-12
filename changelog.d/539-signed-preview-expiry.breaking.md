**A signed preview link always has its expiry** —
`SignedPreviewLink.expires_at` (Python, a `datetime`) / `expiresAt`
(TypeScript, an RFC 3339 string) is required, as the contract requires it:
`signed_preview_link()` / `signedPreviewLink()` refuse a response without it
(Python's was an optional string).
