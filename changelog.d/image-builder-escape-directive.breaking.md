**`ImageBuilder.env()` refuses a non-backslash escape directive** — `env()` on
a builder from a Dockerfile whose parser directive selects another escape
character (`# escape=` followed by a backtick) is refused with
`BadRequestError`, since that character changes how the literal value would be
read.
