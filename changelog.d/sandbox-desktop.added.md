**Create Linux sandboxes with a desktop** — Python's `create(desktop=...)` and
TypeScript's `create(..., { desktop })` select XFCE or GNOME and a supported
resolution; `{}` requests XFCE at 1280x720. `Sandbox.desktop` reports the
resolved create-time settings; computer-use `display.info()` reports the live
display size. Desktop creates must be enabled on the deployment.
