**TypeScript: error names survive minification** — every SDK error class now
sets its name explicitly, so in a minified production bundle `error.name`,
`String(error)` and stack traces read `NotFoundError`,
`ConnectionError`, ... instead of a mangled class name. `instanceof` checks
and the names of your own subclasses are unchanged. TypeScript only: Python
class names are never minified.
