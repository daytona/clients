**`VOLUME_NOT_FOUND` error code** — creating a sandbox with a volume that does
not exist now fails with the code `VOLUME_NOT_FOUND` (still a `NotFoundError`,
HTTP 404), and the message names the missing volume. The code is in both SDKs'
`codes` catalogs.
