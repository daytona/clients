**TypeScript: declarations compile without the DOM lib** — the published type
declarations no longer name the DOM-only types `AddEventListenerOptions`,
`BodyInit` and `DOMException`, so a Node-only project (`lib: ["es2022"]`,
`types: ["node"]`, `skipLibCheck: false`) type-checks the package without
adding `"dom"`, with `@types/node` 20.16.3 or later on the 20.x line, or
22.5.2 or later on the 22.x line.
