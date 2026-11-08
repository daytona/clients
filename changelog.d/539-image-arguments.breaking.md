**Image and artifact arguments are checked** — in both SDKs, before any
request, with `BadRequestError`: the image options of `images.build()` /
`pull()`, `capture_image()` / `captureImage()` and `create_from_build()` /
`createFromBuild()` (checked before the build) must have their types;
`cpu_arch` / `cpuArch` is `amd64` or `arm64`; `idle_expiry_seconds` /
`idleExpirySeconds` is `-1` (the maximum) or a positive integer, as the
contract states; `images.pull()` needs a boolean `wait`. Python
`create_from_build(build_timeout=...)` reads an integer too large for a float
as no limit.
