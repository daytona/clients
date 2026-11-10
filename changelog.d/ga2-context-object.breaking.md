**`DockerfileImageSource` no longer exposes the build context's storage key** —
Python `DockerfileImageSource.context_object` and the TypeScript dockerfile
source's `contextObject` are removed; they carried an internal object-store key
with no use outside the platform. The source keeps `dockerfile`.
