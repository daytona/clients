**CLI: image waits end on an `error` status** — an image that reaches `error`
ends the wait with its recorded error, like `failed`, instead of waiting until
`--timeout`.
