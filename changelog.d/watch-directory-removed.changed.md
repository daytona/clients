**Watching a directory that gets deleted** — `watch_dir()` / `watchDir()` now end without an error when the sandbox
closes the watch after reporting the watched directory's own removal (a `remove` event with an empty `name`):
`on_exit` / `onExit` and `wait_for_exit()` / `waitForExit()` receive no error, as after `stop()`. Before, that end
was reported as a `ConnectionError`. A stream that ends at any other point is still a `ConnectionError`.
