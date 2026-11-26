**Retry settling sandboxes** — SDK fork, image capture and snapshot creation,
and their CLI commands, retry `SANDBOX_TRANSITION_IN_PROGRESS` for up to
30 seconds before acceptance, honoring cancellation. Other conflicts are
returned without retrying.
