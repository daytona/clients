**Interpreter kernel setup failures raise** — Python, TypeScript and Go: a code
run that the sandbox ends before the code reaches a kernel (most often a kernel
that could not be installed or started, for example a first run under a network
policy that blocks PyPI or `dl.deno.land`) now raises `KernelUnavailableError` /
`ErrKernelUnavailable` (503, source `SDK`) carrying the sandbox's setup
diagnostic, instead of returning an empty result with `process.reason ==
"failed"` and no error. Go also returns the partial result alongside the error.
