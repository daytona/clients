**`ImageBuilder` refuses a trailing backslash** — a command, path or other
single-line argument ending in `\` raises `BadRequestError` instead of
continuing the rendered Dockerfile onto the next instruction.
