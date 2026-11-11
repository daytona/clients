**Empty label keys refused locally** — the API refuses a label with an empty
key, so `create*()`, `set_labels()` / `setLabels()` and `search()` labels now
refuse one with `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) before
any request, and `daytona sandbox search --labels` refuses one as a usage
error.
