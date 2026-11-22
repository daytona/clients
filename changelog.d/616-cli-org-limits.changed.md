**`daytona org limits` shows only the count for limits that are not
returned** — the total sandboxes, images, snapshots and volumes rows print the
current count instead of "N / unlimited", since the API does not return
those organization limits. A deployment that still returns one prints
"N / max".
