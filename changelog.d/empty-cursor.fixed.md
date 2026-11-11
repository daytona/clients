**Python iterators stop on an empty cursor** — an `iter()` / `iter_endpoints()`
whose page carries `next_cursor == ""` ends there, as in TypeScript, instead of
fetching the first page again and yielding its items twice.
