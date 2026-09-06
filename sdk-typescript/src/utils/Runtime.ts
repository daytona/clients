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
    if (RUNTIME !== Runtime.NODE || typeof require === 'undefined') return {}
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
      console.warn(
        `\`${name}\` set in a .env or .env.local file was ignored: the Daytona API endpoint is` +
          ` never read from dotenv files, because the working directory is not always authored by` +
          ` you. Using \`${apiUrl}\` instead. To change the endpoint, pass \`apiUrl\` to the Daytona` +
          ` constructor or set \`${name}\` in the environment of the process.`,
      )
    }
  }
}
