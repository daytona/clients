**Build from a local context up to 1 GiB** — `images.build_from_context()` /
`images.buildFromContext()` upload a tar archive (bytes, a path, or in
TypeScript a `Blob`) straight to storage through `POST /build-contexts` and
build from the verified upload, reusing an identical earlier upload instead of
sending it again.
