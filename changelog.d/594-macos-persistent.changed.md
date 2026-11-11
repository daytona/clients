**macOS persistent sandbox docs follow the service** — a persistent macOS
sandbox keeps its disk and machine across stop and start, and its memory
across pause and resume; a start can run on any Mac of the organization
(after a start on a different Mac, iCloud asks the user to sign in again),
and a reservation end or a Mac failure stops a started sandbox and never
deletes it. Only documentation and CLI help changed.
