**macOS creates and starts with no report from the Mac:** if a Mac claims a
macOS create or start and then sends no report on it for 900 seconds, the
platform ends it. A start goes back to `stopped` with its disk kept. For a
create, the platform deletes the sandbox, as at the end of its create budget.
