**`keyboard.type()` delay in seconds** — the pause between key presses is in
seconds, like every other duration: TypeScript `keyboard.type({ text, delay })`
becomes `delaySeconds`, and Python `keyboard.type(text=..., delay=...)` now
reads `delay` as seconds (a float) instead of milliseconds. Divide a 0.x value
by 1000; the SDKs convert it to milliseconds on the wire.
