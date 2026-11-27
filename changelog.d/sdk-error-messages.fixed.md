**Error messages are never empty** — in every SDK, an API or toolbox error
response with an empty or blank body, or an envelope whose `message` and
`error` are empty or blank, reads `HTTP <status>` (`build log HTTP <status>`
for the build-log reads); a JSON body that is not an object, such as `null`,
`"oops"` or an array, is its text as sent, on every TypeScript transport path
too.
