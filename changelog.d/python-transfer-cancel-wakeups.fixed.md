**Python file transfers no longer wake every 50 ms** — `upload_file()` and
`download_file()` with no `cancel` signal wait on the network directly, and an
`asyncio.Event` or `threading.Event` passed as `cancel` stops a stalled
transfer as soon as it is set, with no periodic checks and no helper thread
left behind. Other objects with `is_set()` are still checked every 50 ms
while the transfer waits.
