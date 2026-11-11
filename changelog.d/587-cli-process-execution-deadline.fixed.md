**CLI: observe terminal process timeouts** — process waits distinguish an
expired long poll from a process stopped by its execution deadline, so terminal
attachments finish instead of waiting again for an already-ended process.
