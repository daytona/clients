**Create `cpu_arch` is closed** — `create()` and `create_from_build()` /
`createFromBuild()` take `cpu_arch` / `cpuArch` as one of the platform's
architectures, `amd64` or `arm64` (the contract's `CpuArchitecture`), in both
SDKs; anything else (`x86_64`, a number, a blank string) is `BadRequestError`
naming it before any request, as for the image option. Before, both SDKs sent
any string, and for a non-string Python dropped it silently while TypeScript
sent it.
