**New error `DISK_NOT_READY`:** a macOS start, fork or image capture can answer
`409 DISK_NOT_READY` (source `DAYTONA_API`, `ConflictError` in both SDKs) for a
short time after the sandbox stops. Retry the request later.
