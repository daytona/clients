**macOS create and start time limits** — a macOS create from an image that omits
`create_timeout_seconds` gets 3600 seconds, as any other create does. Before,
this default grew with the image's size. A create from an image captured on an
earlier version of macOS sandboxes gets 86400 seconds. A start that does not
begin on a Mac within 3600 seconds goes back to `stopped`.
