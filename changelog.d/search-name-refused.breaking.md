**`search` is not an image or snapshot name to look up** — `images.get('search')` and
`snapshots.get('search')` in both SDKs, and `daytona image get search` / `daytona snapshot get
search`, now fail with `BadRequestError` (a usage error in the CLI) before any request:
`GET /images/search` and `GET /snapshots/search` are the search routes, so a get of that name
would reach the search route, not the item. Read such an image or snapshot by its ID.
