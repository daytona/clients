**Filesystem options are type-checked** — `fs.list()`'s `depth` must be an
integer, `watch_dir()` / `watchDir()`'s `recursive` a boolean and its event
callback callable; anything else is a `BadRequestError` before any request
(a string depth or recursion flag was sent as is, and a missing callback
ended the watch with a `TypeError`).
