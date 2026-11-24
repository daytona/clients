**Error messages are never empty** — in every SDK, an error response with an
empty or blank body, or an envelope whose `message` and `error` are empty or
blank, reads `HTTP <status>`; a JSON body that is not an object, such as
`null`, is its text.
