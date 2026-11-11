**`ImageBuilder` refuses a trailing backslash** — a command, path or other
single-line argument ending in `\` raises `BadRequestError`: in a raw
instruction it would continue onto the next one, and in a `COPY` path the
Dockerfile parser would drop it.
