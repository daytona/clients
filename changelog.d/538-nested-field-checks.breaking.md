**Python checks nested toolbox fields before decoding** — every list, map and
nested-object field of a toolbox response is checked before Python decodes it:
Python read a `{}` or `""` list as empty (a `run()` whose inline log `frames`
was malformed returned no output) and raised a raw `TypeError` for a number.
This covers `run()` / `exec()` / `session.run()` logs, the display, window and
recording lists and accessibility nodes; TypeScript already refused them.
