**CLI input and result checks** — `sandbox exec` and the other process
commands fail with the process's exit code when the toolbox reports a nonzero
`exitCode` without a `reason`; `--port 8080/` is refused instead of becoming a
TCP mapping; `image build --file` refuses non-string `name`, `region_id`,
`cpu_arch`, `dockerfile` and `context` values instead of ignoring them;
`sandbox update --file` refuses a null lifecycle or label field however its
key is capitalized; `image list --anonymous` takes only `true` or `false`
(the API has no value for both); `project service-account add` resolves a
reused name to the live service account instead of reporting it as ambiguous
with an archived one; and a sandbox lookup whose region list cannot be read
reports that failure instead of "not found".
