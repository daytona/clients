**Python needs `websockets` 13 or later** — the sandbox events connection uses
the `websockets` asyncio client, which refuses a redirected handshake; the
dependency floor moves from 12 to 13.
