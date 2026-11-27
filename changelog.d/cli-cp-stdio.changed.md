**CLI: `sandbox cp` streams `-` and acknowledges a copy** — a local endpoint
of `-` is stdin for an upload and stdout for a download (one file; a file
named `-` is `./-`), where it used to be a local file named `-`. With
`-o json` or `yaml`, a finished copy prints `{"source", "destination",
"bytes"}` (`{"source", "destination", "recursive": true}` with `-r`) instead
of nothing; a download to `-` prints only the file.
