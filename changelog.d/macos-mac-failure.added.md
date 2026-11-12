**macOS sandboxes on a failed Mac:** if a Mac fails before a persistent macOS
sandbox on it starts for the first time, the sandbox goes to `lost`, as an
ephemeral sandbox does. After its first start, a Mac failure stops a persistent
sandbox and never deletes it.
