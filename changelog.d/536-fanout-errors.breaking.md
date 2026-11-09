**CLI: region fan-out errors** — `sandbox list` and `image list` refuse an
unknown `--region` (exit 2, no cursor; it was exit 7). When no region answers,
the command fails with the first failure's exit code and server code, source
and trace id (a refused credential is exit 3, not 7), and the message names
every region's failure. A partial result still exits 7, with each failed
region's `status`, `code`, `source` and `trace_id` in `errors`, a message
naming each region with its status, code and trace id, and no `source` of its
own on the error object.
