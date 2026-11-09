**Toolbox class declarations** — the published TypeScript declarations of the
toolbox classes (process, session, PTY, interpreter, filesystem, git, LSP and
computer-use services and handles) no longer name the SDK's internal
transport types in their constructors: each declares
`constructor(token: Internal, ...init: never[])`, since the SDK builds them.
