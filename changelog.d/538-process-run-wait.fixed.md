TypeScript `run()` without `timeoutSeconds` holds its first request for one
60-second window (`waitMs=60000`), as Python does, before it keeps waiting
without a limit; the result is unchanged.
