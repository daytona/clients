**Filesystem and git input types** — Path lists require sequences (Python) or
arrays (TypeScript) of strings; bare strings and non-string entries are refused
locally with `BadRequestError`. Text writes require strings, binary writes accept
bytes-like buffers (including buffer views), and invalid filesystem depth,
watch recursion and event callbacks are refused before a request is sent.
