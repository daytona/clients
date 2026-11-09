/*
 * Shared helpers for the Daytona TypeScript examples.
 *
 * Every example builds its `Daytona` client from the same environment
 * variables so they can all use the same Daytona deployment:
 *
 *     DAYTONA_API_URL      Daytona API base         (default https://api.daytona.com)
 *     DAYTONA_API_KEY      bearer api key (dtn_...)  (required)
 *     DAYTONA_IMAGE        registry image sandboxes start from (default python:3.12-slim)
 */

import { createHash, randomUUID } from 'node:crypto'

import { Daytona, type DaytonaV2Client } from '@daytona/sdk'
import { NameConflictError, type Image } from '@daytona/sdk/v2'

export const IMAGE = process.env.DAYTONA_IMAGE ?? 'python:3.12-slim'
/**
 * The project image IMAGE is registered as, shared by every example run. The
 * digest of the full reference keeps distinct references apart (the readable
 * part folds ':', '/' and '.' together).
 */
export function imageName(ref: string): string {
  const readable = ref.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
  return `examples-${readable}-${createHash('sha256').update(ref).digest('hex').slice(0, 8)}`
}
export const IMAGE_NAME = imageName(IMAGE)
/** Server waits for a registering image (each returns after at most about 30 s). */
const IMAGE_WAITS = 40
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

/** Build the v2 client (the root's `.v2`) from the shared env vars, or exit with a hint. */
export function client(): DaytonaV2Client {
  const apiKey = process.env.DAYTONA_API_KEY
  if (!apiKey) {
    console.error('DAYTONA_API_KEY is required (mint a project API key from the dashboard)')
    process.exit(2)
  }
  // The v2 client reads DAYTONA_V2_API_URL, else DAYTONA_API_URL; default to the
  // Daytona API when neither is set.
  if (!process.env.DAYTONA_V2_API_URL?.trim() && !process.env.DAYTONA_API_URL?.trim()) {
    process.env.DAYTONA_API_URL = 'https://api.daytona.com'
  }
  // The examples pick their API through DAYTONA_API_URL (the root client has no
  // URL field), which the root's v2 client reads.
  return new Daytona({
    apiKey,
  }).v2
}

async function registered(daytona: DaytonaV2Client): Promise<Image | undefined> {
  for await (const image of daytona.images.iter({ name: IMAGE_NAME })) if (image.name === IMAGE_NAME) return image
  return undefined
}

/**
 * The registered image to create sandboxes from. A sandbox is created from a
 * project image, not a registry reference: reuse IMAGE's registration when the
 * project has one (waiting while it registers), else register it
 * (`images.pull`, `POST /images`). A failed registration is deleted and
 * registered again.
 */
export async function sandboxImage(daytona: DaytonaV2Client): Promise<string> {
  let cause = 'no registration attempt finished'
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let image = await registered(daytona)
    if (image === undefined) {
      info(`registering ${IMAGE} as image ${IMAGE_NAME}`)
      try {
        image = await daytona.images.pull(IMAGE, { name: IMAGE_NAME, wait: false })
      } catch (error) {
        if (error instanceof NameConflictError) continue // Another run registered it first: use that one.
        throw error
      }
    }
    // The server's wait returns after a while even when the image is not done.
    for (let wait = 0; wait < IMAGE_WAITS && !['ready', 'failed', 'error'].includes(image.status); wait += 1) {
      image = await daytona.images.get(image.id, { wait: 'ready' })
    }
    if (image.status === 'ready') return IMAGE_NAME
    if (image.status !== 'failed' && image.status !== 'error') throw new Error(`image ${IMAGE_NAME} is still ${image.status}`)
    // A failed registration: report why, then register again.
    cause = image.error ?? `registration ${image.status}`
    info(`image ${IMAGE_NAME} failed: ${cause}; registering again`)
    await daytona.images.delete(image.id)
  }
  throw new Error(`could not register ${IMAGE} as image ${IMAGE_NAME}: ${cause}`)
}
