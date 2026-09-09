/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

declare global {
  /**
   * In Deno this global exists and has a `version.deno` string;
   * in all other runtimes it will be `undefined`.
   */
  var Deno:
    | {
        version: { deno: string }
        env: {
          get(name: string): string | undefined
          toObject(): Record<string, string>
        }
      }
    | undefined

  /**
   * In Bun this global exists and has a `version.bun` string;
   * in all other runtimes it will be `undefined`.
   */
  var Bun:
    | {
        version: { bun: string }
        file: (path: string) => File
      }
    | undefined
}

export enum Runtime {
  NODE = 'node',
  DENO = 'deno',
  BUN = 'bun',
  BROWSER = 'browser',
  SERVERLESS = 'serverless',
  UNKNOWN = 'unknown',
}

export const RUNTIME =
  typeof Deno !== 'undefined'
    ? Runtime.DENO
    : typeof Bun !== 'undefined' && !!Bun.version
      ? Runtime.BUN
      : isServerlessRuntime()
        ? Runtime.SERVERLESS
        : typeof window !== 'undefined'
          ? Runtime.BROWSER
          : typeof process !== 'undefined' && !!process.versions?.node
            ? Runtime.NODE
            : Runtime.UNKNOWN

export function getEnvVar(name: string): string | undefined {
  if (typeof process !== 'undefined' && process.env) {
    return process.env[name]
  }
  if (RUNTIME === Runtime.DENO) {
    return Deno.env.get(name)
  }

  return undefined
}

/**
 * Loads a CommonJS module, or returns undefined when one cannot be loaded.
 *
 * Reaching `require` through a real call is deliberate. `require` is not defined in an ES
 * module, so post-build.js rewrites the call below onto a `createRequire` shim in the
 * published ESM build; a `typeof require` test inlined at a call site is not a call, is
 * not rewritten, and so reports "no require" in ESM even though one is reachable. Every
 * caller must therefore go through a real call rather than test for the binding.
 *
 * A bundler can also leave `require` defined while a given specifier is unresolvable, so
 * the failure is swallowed either way: these callers must degrade, never throw.
 *
 * Left untyped on purpose - a `typeof import(...)` annotation here pulls the node typings
 * in differently and reorders unrelated unions in the generated docs.
 */
function tryRequire(id: string): any | undefined {
  try {
    return require(id)
  } catch {
    return undefined
  }
}

export class DaytonaEnvReader {
  private readonly envLocalVars: Record<string, string>
  private readonly envVars: Record<string, string>

  constructor() {
    this.envLocalVars = DaytonaEnvReader.parseFileVars('.env.local')
    this.envVars = DaytonaEnvReader.parseFileVars('.env')
  }

  get(name: string): string | undefined {
    DaytonaEnvReader.checkName(name)
    // 1. Runtime env
    const runtimeVal = getEnvVar(name)
    if (runtimeVal !== undefined) return runtimeVal
    // 2. .env.local, 3. .env
    return this.getFromFile(name)
  }

  /**
   * Reads `name` from the process environment only, never from .env / .env.local.
   *
   * The dotenv files are read from the current working directory, which is not
   * necessarily authored by whoever runs the process. Anything that determines the host a
   * credential is sent to must be resolved through this method, so that a file in the
   * working directory cannot redirect the SDK.
   */
  getFromProcessEnv(name: string): string | undefined {
    DaytonaEnvReader.checkName(name)
    return getEnvVar(name)
  }

  /** Reads `name` from .env.local / .env only, ignoring the process environment. */
  getFromFile(name: string): string | undefined {
    DaytonaEnvReader.checkName(name)
    if (name in this.envLocalVars) return this.envLocalVars[name]
    return this.envVars[name]
  }

  private static checkName(name: string): void {
    if (!name.startsWith('DAYTONA_')) {
      throw new Error(`DaytonaEnvReader: variable name must start with 'DAYTONA_', got '${name}'`)
    }
  }

  private static parseFileVars(path: string): Record<string, string> {
    // Bun is detected before Node above, so a Node-only gate would leave the reader unable to
    // read files on the one runtime that pre-loads them. Bun implements the CommonJS
    // module loader, so the require below resolves there.
    if (RUNTIME !== Runtime.NODE && RUNTIME !== Runtime.BUN) return {}
    const fs = tryRequire('fs')
    const dotenv = tryRequire('dotenv')
    if (!fs || !dotenv) return {}
    if (!fs.existsSync(path)) return {}
    const parsed = dotenv.parse(fs.readFileSync(path)) as Record<string, string>
    return Object.fromEntries(Object.entries(parsed).filter(([k]) => k.startsWith('DAYTONA_')))
  }
}

export function isServerlessRuntime(): boolean {
  // Safely grab env vars, even if `process` is undeclared
  const env = typeof process !== 'undefined' ? process.env : {}

  // Worker-specific globals
  const globalObj = globalThis as any

  return Boolean(
    // Cloudflare Workers (V8 isolate API)
    typeof globalObj.WebSocketPair === 'function' ||
    // Cloudflare Pages
    env.CF_PAGES === '1' ||
    // AWS Lambda (incl. SAM local)
    env.AWS_EXECUTION_ENV?.startsWith('AWS_Lambda') ||
    env.LAMBDA_TASK_ROOT !== undefined ||
    env.AWS_SAM_LOCAL === 'true' ||
    // Azure Functions
    env.FUNCTIONS_WORKER_RUNTIME !== undefined ||
    // Google Cloud Functions / Cloud Run
    (env.FUNCTION_TARGET !== undefined && env.FUNCTION_SIGNATURE_TYPE !== undefined) ||
    // Vercel
    env.VERCEL === '1' ||
    // Netlify Functions
    env.SITE_NAME !== undefined,
  )
}

/**
 * Warns when a dotenv file asked for an API endpoint that was not used.
 *
 * Staying silent when the file value matches the endpoint in use keeps the documented
 * `.env` layout quiet, while a file that would have changed the destination is reported.
 */
export function warnIfDotenvApiUrlIgnored(reader: DaytonaEnvReader, apiUrl: string): void {
  for (const name of ['DAYTONA_API_URL', 'DAYTONA_SERVER_URL']) {
    const fileValue = reader.getFromFile(name)
    if (fileValue && fileValue !== apiUrl) {
      // One report per construction: a file that sets both endpoint variables to the same
      // redirected value does not need saying twice.
      console.warn(
        `\`${name}\` set in a .env or .env.local file was ignored: the Daytona API endpoint is` +
          ` never read from dotenv files, because the working directory is not always authored by` +
          ` you. Using \`${apiUrl}\` instead. To change the endpoint, pass \`apiUrl\` to the Daytona` +
          ` constructor or set \`${name}\` in the environment of the process.`,
      )
      return
    }
  }
}

/** The variables that decide which host the API key is sent to. */
const ENDPOINT_VARS = ['DAYTONA_API_URL', 'DAYTONA_SERVER_URL'] as const

/**
 * Matches an assignment to an endpoint variable in a dotenv file.
 *
 * The two variable names must stay in step with `ENDPOINT_VARS` above. A literal regex is
 * clearer — and faster — than deriving the pattern at runtime.
 *
 * No `g` flag: `.test()` is stateless on a non-global regex, so the same instance can be
 * reused across files without resetting `lastIndex`.
 */
const ENDPOINT_ASSIGNMENT = /^[ \t]*(?:export[ \t]+)?(?:DAYTONA_API_URL|DAYTONA_SERVER_URL)[ \t]*=/m

/**
 * The union of the dotenv file names that Bun and Next.js load on their own. `.env` and
 * `.env.local` are the SDK's own precedence chain; the rest are here only so that a file
 * defining the endpoint cannot go unnoticed.
 */
const PRELOADABLE_DOTENV_FILES = [
  '.env',
  '.env.local',
  '.env.development',
  '.env.development.local',
  '.env.production',
  '.env.production.local',
  '.env.test',
  '.env.test.local',
] as const

/**
 * Whether either endpoint variable is present in the process environment.
 *
 * Enumerating loaders (Bun, `--env-file`, Next.js, Deno, Nuxt/Nitro, `@next/env`, …) can
 * never be complete. The endpoint can only come from the constructor or `process.env`, so
 * the precise condition is: a working-directory file names the variable **and** that variable
 * is present in the process environment. This is both broader (covers every loader) and
 * narrower (does not fire when a file exists but nothing loaded it).
 */
export function endpointNamedInProcessEnv(): boolean {
  return ENDPOINT_VARS.some((name) => getEnvVar(name) !== undefined)
}

/**
 * The working directory as it was when this module first loaded.
 *
 * A runtime resolves its dotenv files when the process starts, so a relative `--env-file`
 * path - and the conventional names - belong to that directory. An application is free to
 * call `process.chdir()` afterwards, which would otherwise move the search away from the
 * file that actually supplied the environment.
 *
 * Imports normally run before application logic, so this is the startup directory in
 * practice. It is not guaranteed: an application that changes directory and only then
 * imports this SDK - a dynamic import, for instance - leaves no way to recover the
 * directory the runtime actually read from, since no runtime exposes its startup working
 * directory. In that case a relative dotenv file goes unseen and a pre-loaded endpoint is
 * trusted. Refusing every pre-loaded endpoint instead would reject the platform-supplied
 * environment variables that Next.js deployments legitimately rely on, so the narrower
 * exposure is preferred here and stated rather than assumed away.
 */
const STARTUP_CWD = typeof process !== 'undefined' && typeof process.cwd === 'function' ? safeCwd() : undefined

function safeCwd(): string | undefined {
  try {
    return process.cwd()
  } catch {
    return undefined
  }
}

/**
 * The dotenv paths a runtime or preloader was told to read, in the order they were given.
 *
 * A runtime told to load a specific file does not restrict itself to the conventional names,
 * so those paths have to be examined too. Relative paths are left as given: the runtime
 * resolved them against the working directory and so does `fs`.
 */
function explicitDotenvPaths(): string[] {
  if (typeof process === 'undefined') return []
  const execArgv = Array.isArray(process.execArgv) ? process.execArgv : []
  const paths: string[] = []
  for (let i = 0; i < execArgv.length; i++) {
    const arg = execArgv[i]
    if (!arg.startsWith('--env-file')) continue
    const separator = arg.indexOf('=')
    if (separator !== -1) {
      const value = arg.slice(separator + 1)
      if (value) paths.push(value)
    } else if (execArgv[i + 1] && !execArgv[i + 1].startsWith('-')) {
      paths.push(execArgv[++i])
    }
  }
  // The dotenv preloader takes its path from DOTENV_CONFIG_PATH or from a
  // `dotenv_config_path=` command-line argument. Its default, `.env`, is already covered by
  // PRELOADABLE_DOTENV_FILES; a configured path is not.
  const configPath = process.env?.DOTENV_CONFIG_PATH
  if (configPath) paths.push(configPath)
  const argv = Array.isArray(process.argv) ? process.argv : []
  for (const arg of argv) {
    if (!arg.startsWith('dotenv_config_path=')) continue
    const value = arg.slice('dotenv_config_path='.length)
    if (value) paths.push(value)
  }
  return paths
}

/**
 * The directories a pre-loading runtime could have read a dotenv file from: the current one,
 * and the startup one when the application has since changed directory.
 */
export function dotenvSearchDirs(): string[] {
  const dirs: string[] = []
  const current = safeCwd()
  if (current) dirs.push(current)
  if (STARTUP_CWD && STARTUP_CWD !== current) dirs.push(STARTUP_CWD)
  return dirs
}

/**
 * The first dotenv file that defines an endpoint variable, if any.
 *
 * The file is scanned as raw text rather than parsed with the `dotenv` package: only the
 * variable's *presence* matters (never its value), and reading the name directly means the
 * check still works where the `dotenv` package is not installed — which is the case when a
 * runtime loads the file itself. A value comparison cannot establish where an environment
 * value came from: runtimes expand `$VAR` references while a parser returns the raw text,
 * so equal-looking values prove nothing and unequal ones rule nothing out.
 */
export function findDotenvFileDefiningEndpoint(searchDirs: string[] = dotenvSearchDirs()): string | undefined {
  if (RUNTIME !== Runtime.NODE && RUNTIME !== Runtime.BUN) return undefined
  // Constructing a client must not fail because the check could not run, so a scan that
  // throws is treated as nothing found: the endpoint then resolves as it did before this
  // check existed.
  try {
    return scanForDotenvEndpoint(searchDirs)
  } catch {
    return undefined
  }
}

function scanForDotenvEndpoint(searchDirs: string[]): string | undefined {
  const fs = tryRequire('fs')
  const nodePath = tryRequire('path')
  if (!fs || !nodePath) return undefined
  const names = [...explicitDotenvPaths(), ...PRELOADABLE_DOTENV_FILES]
  const candidates: string[] = []
  for (const name of names) {
    for (const dir of searchDirs) candidates.push(nodePath.resolve(dir, name))
  }
  for (const file of [...new Set(candidates)]) {
    if (!fs.existsSync(file)) continue
    let text: string
    try {
      // Editors on Windows write a byte-order mark and the loaders tolerate it, but `^`
      // would not match an assignment sitting on the first line behind one.
      text = (fs.readFileSync(file, 'utf8') as string).replace(/^\uFEFF/, '')
    } catch {
      continue
    }
    if (ENDPOINT_ASSIGNMENT.test(text)) return file
  }
  return undefined
}
