**TypeScript: requests work in workerd-based edge runtimes with axios 1.20** —
axios 1.20 sends `cache: 'default'` on every fetch, which workerd rejects
("Unsupported cache mode: default"), so every API and toolbox request failed
there. The SDK now sends `cache: 'no-store'` in runtimes whose `Request`
refuses the default mode; Node.js, Deno, Bun and browsers send requests
unchanged. TypeScript only: the Python SDK does not run on fetch-based
runtimes.
