**Deleted and deleting sandboxes** — setting labels, updating the lifecycle
policy, resetting the idle timer, and exposing, listing or minting preview
links for ports (`expose`, `list_endpoints` / `listEndpoints`, `preview_link`
/ `previewLink`, `signed_preview_link` / `signedPreviewLink`) on a sandbox
that is deleted or being deleted raise `ConflictError` (`CONFLICT`) from the
API; code that expected these calls to succeed, or to get `NotFoundError`, on
such a sandbox should catch `ConflictError`. Removing a port's endpoint and
revoking a signed preview link still work.
