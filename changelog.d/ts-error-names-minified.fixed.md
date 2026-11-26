**TypeScript: error names survive minification** — the error classes of the
package root (`@daytona/sdk`) and of `@daytona/sdk/v2` now set their names
explicitly, so in a minified production bundle `error.name`, `String(error)`
and stack traces read `NotFoundError`, `ConnectionError`, ... instead of a
mangled class name. `instanceof` checks and the names of your own subclasses
are unchanged. The bundled v0 namespace's error classes are the 0.x SDK's and
keep their 0.x behaviour. TypeScript only: Python class names are never
minified.
