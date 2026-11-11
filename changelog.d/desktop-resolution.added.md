**Desktop resolution** — computer use `start()` and `restart()` take an
optional `resolution` (TypeScript `{ resolution: { width, height } }`, Python
`resolution=ComputerUseResolution(width=…, height=…)`): 1024x768, 1280x720 (the
default) or 1920x1080. The selection is kept for later starts and restarts;
starting a running desktop at a different resolution raises a conflict error
(restart with it instead). Linux sandboxes only; other platforms refuse a
`resolution`, and a sandbox started before its platform supported this
ignores it.
