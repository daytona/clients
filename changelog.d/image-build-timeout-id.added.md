**`ImageBuildTimeoutError` names the still-building image** — when
`create_from_build()` / `createFromBuild()` / `CreateFromBuild` (and Go's
`Images().BuildAndWait`) runs out of its build budget after the server
accepted the build, the error now carries the image's id (`image_id` /
`imageId` / `ImageID`), so the build that keeps running can be followed,
waited on or deleted. It is a `RequestTimeoutError` (Go: it matches
`ErrRequestTimeout`) with the same status, code, source and message as
before, so existing handlers keep catching it.
