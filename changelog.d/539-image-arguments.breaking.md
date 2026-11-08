**Image and artifact arguments are checked** — in both SDKs, before any
request, with `BadRequestError` naming the argument. The image options of
`images.build()` / `pull()` and `create_from_build()` / `createFromBuild()`
(checked before the build): `name` and `region` are strings, `cpu_arch` /
`cpuArch` is `amd64` or `arm64`, and `idle_expiry_seconds` /
`idleExpirySeconds` is `-1` (the maximum) or a positive integer, as the
contract states; `capture_image()` / `captureImage()` takes the same `name`
and `idle_expiry_seconds` / `idleExpirySeconds` rules. `images.pull()` needs a
boolean `wait`. `create_from_build(build_timeout=...)` / `buildTimeoutSeconds`
is a non-negative number of seconds, and Python reads an integer too large for
a float there as no limit.
