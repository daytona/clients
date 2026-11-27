**Search ordering and lag** — `search()` returns results in `sort` order
(creation time by default), not by relevance, and `find_by_name()` returns
newest first; both can lag a create, delete or label change by a few seconds.
