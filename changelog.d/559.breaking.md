**Volume type chosen by the server** — `volumes.create()` (and
`volumes.get(..., create=True)` / `{ create: true }`) no longer sends
`type: "disk"`: without a type, the server chooses the volume's type, which
may not be `disk`. Pass `type="disk"` / `{ type: 'disk' }` (sized with
`size_gib` / `sizeGib`) or `type="bucket"` / `{ type: 'bucket' }` to choose
it; any other value is a `BadRequestError` before any request. The returned
`Volume.type` reports the type the server chose.
