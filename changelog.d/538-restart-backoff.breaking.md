**Computer-use restart backoff in seconds** — `ComputerUseProcessStatus`
reports the add-on process's restart backoff in seconds, like every other
duration: Python `restart_backoff_ms` becomes `restart_backoff` (a float) and
TypeScript `restartBackoffMs` becomes `restartBackoffSeconds`.
