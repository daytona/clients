**Volume sizes are int32** — `volumes.create()` refuses a `size_gib` /
`sizeGib` above 2147483647 with `BadRequestError` before any request, in both
SDKs; the API declares it an int32.
