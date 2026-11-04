**macOS create and start time budgets:** a macOS create from a saved image that
omits `create_timeout_seconds` gets 3600 seconds, as any other create does,
instead of 3600 plus the time to download the image at the platform's minimum
restore rate. A create from an older saved image, which converts first, keeps
a longer default, 86400 seconds. A start that no Mac takes within 3600 seconds
goes back to `stopped`.
