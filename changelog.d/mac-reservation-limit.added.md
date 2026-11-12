**Mac reservation limit** — the organization limits read (`get_limits()` /
`getLimits()`, `daytona org limits`) carries an optional `mac_reservations` /
`macReservations` group (`limit`, `usage`): how many Macs the organization holds
at once (pending, active or ending reservations) and its plan's cap. A purchase
over the cap raises `LimitExceededError` with `limit` `max_mac_reservations`: a
single reservation, a macOS create that buys one, or a bulk purchase when fewer
than `min_count` fit under the cap (a bulk purchase reserves only as many as
fit). A renewal (`reservations.renew()`) that would keep the organization over
the cap raises `LimitExceededError` with `limit` `max_mac_reservations` too. The
CLI table shows a "Mac reservations" row when the API sends the group, and a
purchase refused at the cap hints how a reservation frees (let an active one
lapse with `--auto-renew=false`, or upgrade the plan) instead of advising to
stop or delete sandboxes.
