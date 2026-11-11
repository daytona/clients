**Capturing an image or snapshot needs `write:sandboxes`** —
`capture_image()` / `captureImage()`, `create_snapshot()` / `createSnapshot()`
and the CLI's `image capture` and `snapshot create` now need `write:sandboxes`
in addition to `write:images` / `write:snapshots`; without it they get
`ForbiddenError` (403, CLI exit code `4`). A key or member role that could
only manage images or snapshots can no longer capture from a sandbox.
