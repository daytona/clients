**CLI: `sandbox cp` streams `-` and acknowledges a copy** — a local endpoint
of `-` is stdin for an upload and stdout for a download (one file), where it
used to be a local file named `-`. Breaking for a script that copied to or
from a file named `-`: name it `./-`. In JSON or YAML output (the default when
stdout is not a terminal, or `-o json|yaml`) a finished copy prints
`{"source", "destination", "bytes"}` (`{"source", "destination",
"recursive": true}` with `-r`), with `destination` the path the file or
directory landed at, instead of nothing; a download to `-` prints only the
file.
