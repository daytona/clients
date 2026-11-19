**Desktop environment selection** — computer use `start()` and `restart()`
accept `environment` (`xfce` or `gnome`), kept for later starts; omitted, the
last selected environment is reused, or xfce. GNOME needs a system layer with
GNOME, at least 4 vCPUs and 8192 MiB. Windows and macOS refuse the option.
`status().environment` reports the running environment, otherwise the next
start's; it is absent on other platforms and from older sandboxes. Future
values read as `unknown`, with the original in TypeScript `rawEnvironment`
or Python `raw_environment`. With either setting, `start()` and `restart()`
share one status read and raise `UnsupportedOperationError` if the requested
environment is absent or differs. Guests with resolution selection but without
environment selection refuse `environment` with 422 `UnprocessableEntityError`;
older guests ignore the body and start XFCE, which the read-back detects.
