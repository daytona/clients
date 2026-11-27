**TypeScript runtime setup** — the TypeScript README covers Deno (unrestricted
`--allow-env`), Bun (the process stays alive until the waits' socket closes,
10 s after the last wait, unless `close()` is called) and Node.js ES module
bundles from esbuild (a `createRequire` banner).
