**Uploads without `atomic`** — an upload without `atomic=True` / `atomic: true` writes the destination in place, so a cancelled or failed upload can leave it truncated.
