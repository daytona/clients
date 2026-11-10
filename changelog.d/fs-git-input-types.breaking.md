**Path lists must be lists of strings** — where `sandbox.fs` and `sandbox.git`
take several paths (`read_many()` / `readMany()`, `replace()`, `git.add()`,
`git.restore()`, `git.reset(files=...)`), a bare string is refused with
`BadRequestError` instead of being split into one path per character (Python;
TypeScript spread it the same way at run time), and so is anything but a
sequence (Python) or array (TypeScript) of strings.
