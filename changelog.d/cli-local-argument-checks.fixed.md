**CLI: invalid arguments fail locally** — an empty or blank positional
argument naming a resource (`daytona sandbox get ''`, `sandbox
create-from-snapshot ''`, `reservation get ''`, `registry get ' '`, and every
other command that takes an id, a name, a host or a path) is a usage error
(exit 2) before any credential or request, instead of reaching the API as a
collection path or an unknown name. A `--file` that holds a JSON array or
another non-object value says so (`--file body.json must contain a JSON
object, got an array`), and a request field of the wrong JSON type is named
(`field "cpu" cannot be a string`) instead of printing a Go decoder error.
