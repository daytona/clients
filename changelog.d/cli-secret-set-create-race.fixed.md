**CLI: `secret set` no longer overwrites a secret created concurrently** —
when the secret did not exist at its read, the write is sent with
`If-None-Match: *`, so a secret another caller created in the meantime is
reported (412 `SECRET_ALREADY_EXISTS`) instead of replaced. Updates of an
existing secret are still last-writer-wins, as the help now says: the API
has no conditional update.
