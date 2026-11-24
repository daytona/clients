**TypeScript: declarations compile without the DOM lib** — the published type
declarations no longer name the DOM-only types `AddEventListenerOptions` and
`BodyInit`, so a Node-only project (`lib: ["es2022"]`, `types: ["node"]`,
`skipLibCheck: false`) type-checks the package without adding `"dom"`.
