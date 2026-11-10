**Path lists must be lists of strings** — where `sandbox.fs` and `sandbox.git`
take several paths (`read_many()` / `readMany()`, `replace()`, `git.add()`,
`git.restore()`, `git.reset(files=...)`), both SDKs refuse a bare string with
`BadRequestError`; before, it was split into one path per character (Python,
and TypeScript at run time). Anything but a sequence (Python) or array
(TypeScript) of strings is refused too.
