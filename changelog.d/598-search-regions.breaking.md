**Sandbox search by region** — the `region` filter of `search()` takes region
IDs: a region that contains other regions (a geo group, or `earth`) matches
every region inside it, and an unknown region is a `BadRequestError` from the
API.
