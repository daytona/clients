**Volume error codes** — both SDKs' `codes` catalog knows two more API codes.
`VOLUME_FINAL_COMMIT_PENDING` (409, `ConflictError`): a create that mounts a
volume is refused while an ended sandbox's last writes to it are not saved
yet; retry once the volume's pending final commits list is empty.
`VOLUMES_UNAVAILABLE` (503, `ServiceUnavailableError`): a sandbox with volumes
cannot be stopped or deleted right now; retry shortly. The CLI adds a hint to
both.
