**Secret and volume lists read search first** — `secrets.list()` / `iter()`,
`volumes.list()` / `iter()`, `daytona secret list` and `daytona volume list`
now read the eventually consistent search routes and fall back to the list
routes only on 501 `SEARCH_NOT_CONFIGURED`, 404 from an older API, or a 200
item response from an older API where `/search` was still a resource name. A
503 `SEARCH_UNAVAILABLE` remains an error. A remembered fallback that later
answers 410 `GONE` retries search.
