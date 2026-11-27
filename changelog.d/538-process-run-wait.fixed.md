**One `run()` wait window** — TypeScript `run()` without `timeoutSeconds`
holds its first request for one wait round (`waitMs=30000`, see "Bounded
process waits"), as Python does, before it keeps waiting in rounds with no
overall limit; the result is unchanged.
