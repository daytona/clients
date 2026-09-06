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
    // read files on the one runtime that pre-loads them. Bun implements require('fs').
    if ((RUNTIME !== Runtime.NODE && RUNTIME !== Runtime.BUN) || typeof require === 'undefined') return {}
    const fs = require('fs')
    if (!fs.existsSync(path)) return {}
    const dotenv = require('dotenv')
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
 * Whether the runtime may have merged a working-directory dotenv file into `process.env`
 * before any user code ran.
 *
 * Bun does this unconditionally unless started with `--no-env-file`; Node does it when given
 * `--env-file`; Next.js does it in its server runtimes. On these runtimes the process
 * environment cannot be attributed to the caller, so it cannot be trusted to name a host.
 */
export function dotenvMayBePreloaded(): boolean {
  if (typeof process === 'undefined') return false
  const execArgv = Array.isArray(process.execArgv) ? process.execArgv : []
  if (execArgv.some((arg) => arg.startsWith('--env-file'))) return true
  if (RUNTIME === Runtime.BUN) return !execArgv.includes('--no-env-file')
  return Boolean(process.env?.NEXT_RUNTIME)
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
 * The paths named by `--env-file` / `--env-file-if-exists`, in the order they were given.
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
 * This deliberately reports on the presence of the *name* and never looks at the value. A
 * value comparison cannot establish where an environment value came from: runtimes expand
 * `$VAR` references while a parser returns the raw text, so equal-looking values prove
 * nothing and unequal ones rule nothing out.
 */
export function findDotenvFileDefiningEndpoint(searchDirs: string[] = dotenvSearchDirs()): string | undefined {
  if ((RUNTIME !== Runtime.NODE && RUNTIME !== Runtime.BUN) || typeof require === 'undefined') return undefined
  // A bundler can leave `require` defined while these are unresolvable. Constructing a
  // client must not fail because the check could not run, so treat that as nothing found:
  // the endpoint then resolves as it did before this check existed.
  let fs: typeof import('fs')
  let nodePath: typeof import('path')
  let dotenv: typeof import('dotenv')
  try {
    fs = require('fs')
    nodePath = require('path')
    dotenv = require('dotenv')
  } catch {
    return undefined
  }
  const names = [...explicitDotenvPaths(), ...PRELOADABLE_DOTENV_FILES]
  const candidates: string[] = []
  for (const name of names) {
    for (const dir of searchDirs) candidates.push(nodePath.resolve(dir, name))
  }
  for (const file of [...new Set(candidates)]) {
    if (!fs.existsSync(file)) continue
    let names: string[]
    try {
      names = Object.keys(dotenv.parse(fs.readFileSync(file)) as Record<string, string>)
    } catch {
      continue
    }
    if (names.some((name) => (ENDPOINT_VARS as readonly string[]).includes(name))) return file
  }
  return undefined
}
