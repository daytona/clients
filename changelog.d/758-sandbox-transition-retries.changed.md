**Retry settling sandboxes** — SDK fork, image capture and snapshot creation,
and their CLI commands, retry `SANDBOX_TRANSITION_IN_PROGRESS` for up to
30 seconds from the first submission to admit retries. Each admitted POST keeps
its normal request timeout. Other conflicts are returned without retrying.
Waiting forks use the source's state after a retried submission is accepted.
