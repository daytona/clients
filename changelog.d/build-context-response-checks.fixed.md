**TypeScript checks the build-context upload response** — `buildFromContext()`
checks the upload response's `id`, `region_id` and `parts` before using them,
as Python does: a malformed one is `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`) before any other request, instead of a build sent without its
context, a raw `TypeError`, or retried part uploads to an unusable URL.
