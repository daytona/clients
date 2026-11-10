**Python needs `websockets` 13 or later** — the sandbox events connection now
uses the `websockets` asyncio client (`websockets.asyncio.client`, added in 13)
so that it can refuse a redirected handshake; the dependency floor moves from
12 to 13.
