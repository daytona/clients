**`MEMBERSHIP_EDITS_FROZEN` error code** — invitations, member updates and
member removals are refused with this 409 while membership changes in the
organization are paused. Both SDKs raise `ConflictError` for it.
