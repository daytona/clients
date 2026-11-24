**Python file transfers no longer wake every 50 ms without a reason** —
`upload_file()` and `download_file()` with no `cancel` signal wait on the
network directly, and a `threading.Event` passed as `cancel` stops a stalled
transfer as soon as it is set, from any thread, with no periodic checks and no
helper thread left behind (on CPython 3.10 to 3.15; elsewhere it is checked
every 50 ms). Other objects with `is_set()`, `asyncio.Event`
included, are still checked every 50 ms while the transfer waits; an
`asyncio.Event` set on the loop running an async transfer also stops it at
once.
