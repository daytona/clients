**`ImageBuilder.env()` values are literal** — an env value reaches the image
exactly as given: `$` is escaped in the rendered Dockerfile, so
`env("PATH", "/opt/bin:$PATH")` sets the literal string instead of expanding
the build-time `PATH` (both SDKs rendered the value as a JSON string, which
Docker still substituted), and a value with a control character (C0 or DEL,
such as a newline or a tab, which Docker kept as a literal `\n` / `\t`) is
refused with `BadRequestError`. Both SDKs render the same bytes.
`env()` on a builder from a Dockerfile whose parser directive selects another
escape character (`# escape=` followed by a backtick) is refused, since that
character changes how the value would be read.
