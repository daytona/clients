/*
 * Shared helpers for the Daytona TypeScript examples.
 *
 * Every example builds its `Daytona` client from the same environment
 * variables so they can all use the same Daytona deployment:
 *
 *     DAYTONA_V2_API_URL   Daytona API base         (default https://api.daytona.com;
 *                          regional endpoint or test environment)
 *     DAYTONA_API_KEY      bearer api key (dtn_...)  (required)
 *     DAYTONA_IMAGE        default sandbox image    (default python:3.12-slim)
 */

import { randomUUID } from 'node:crypto'

import { Daytona, type DaytonaV2Client } from '@daytona/sdk'
import type { Sandbox } from '@daytona/sdk/v2'

export const IMAGE = process.env.DAYTONA_IMAGE ?? 'python:3.12-slim'
// A CSPRNG, not Math.random(): the secrets example derives a demo secret
// value from RUN.
export const RUN = randomUUID().replace(/-/g, '').slice(0, 8)

const BOLD = '\x1b[1m'
const GREEN = '\x1b[32m'
const RED = '\x1b[31m'
const RESET = '\x1b[0m'

export const section = (title: string): void => console.log(`\n${BOLD}== ${title}${RESET}`)
export const info = (msg: string): void => console.log(`   .. ${msg}`)
export const pass = (msg: string): void => console.log(`   ${GREEN}PASS${RESET} ${msg}`)
export const fail = (msg: string): void => console.log(`   ${RED}FAIL${RESET} ${msg}`)
export const sleep = (s: number): Promise<void> => new Promise((r) => setTimeout(r, s * 1000))

export function assertStarted(sandbox: Sandbox): void {
  if (sandbox.state === 'started') return
  const raw = sandbox.state === 'unknown' ? `, rawState=${sandbox.rawState ?? ''}` : ''
  throw new Error(`sandbox ${sandbox.id} did not start (state=${sandbox.state}${raw})`)
}

/** Build the v2 client (the root's `.v2`) from the shared env vars, or exit with a hint. */
export function client(): DaytonaV2Client {
  const apiKey = process.env.DAYTONA_API_KEY
  if (!apiKey) {
    console.error('DAYTONA_API_KEY is required (mint a project API key from the dashboard)')
    process.exit(2)
  }
  // The root client has no URL field: the v2 client reads DAYTONA_V2_API_URL
  // itself and defaults to the Daytona API when it is unset.
  return new Daytona({
    apiKey,
  }).v2
}
