**SDK reference docs** — README sections and docstrings that contradicted the
SDKs are corrected: `close_stdin()` / `closeStdin()` is for exec handles (a PTY
needs `"\x04"`), `glob()` matches directories and the root and supports only
`*` and `?`, capture-image examples split named and anonymous images, session
examples run on a stock image, the removed `SandboxCreateTimeoutError` is no
longer listed, the v0 client import guard is described per module format, and
the lifecycle, fork and snapshot docs say which operations need a persistent
sandbox. New sections document the account methods, the remaining git
operations, retry and `Retry-After` behaviour, client timeouts that do not
cancel remote work, the interpreter's interrupt grace, and non-atomic upload
truncation.
