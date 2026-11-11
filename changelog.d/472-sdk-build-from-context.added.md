**Build from a local context up to 1 GiB** — `images.build_from_context()` /
`images.buildFromContext()` declare a tar archive (any bytes-like value, a path, or in
TypeScript a `Blob`) with `POST /build-contexts`, upload its parts straight to
storage through presigned URLs, and build from the verified upload, reusing an
identical earlier upload instead of sending it again.
