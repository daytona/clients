**macOS sandbox docs follow the current service behaviour** — a persistent
macOS sandbox keeps its disk and machine across stop and start, and a start
can run on any Mac of the organization (after a start on a different Mac,
iCloud asks the user to sign in again); a reservation end or a Mac failure
stops a started sandbox and never deletes it. Omitted `create_macos` /
`createMacos` and `daytona sandbox create-macos` sizes use the Mac model's
`default_size` / `defaultSize`, or the image's disk size for a create from a
captured image. Only documentation and CLI help changed.
