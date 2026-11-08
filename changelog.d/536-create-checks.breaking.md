**CLI: create inputs checked locally** — `sandbox create --cpu-max` (or
`cores_max` in `--file`) must be a positive multiple of 0.5 cores, and `image
create --file` refuses a `dockerfile` field (use `image build`); both are
usage errors before any request.
