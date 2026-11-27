/*
 * images — build a reusable image, then boot a sandbox from it.
 *
 * Builds a named image from an inline Dockerfile (`images.build`), waits for
 * that image by reading it directly (`images.get(id, { wait: 'ready' })`),
 * creates a sandbox from it, and deletes both the sandbox and the image the
 * run built, also when the build, the wait or the create fails. A build whose
 * submission succeeded but whose reply was lost is found by its unique name,
 * and an image delete refused while the sandbox still releases it (409) is
 * retried for a short while.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/images/index.ts
 */

import { ConflictError, ImageBuilder, NotFoundError, RequestTimeoutError, type Image } from '@daytona/sdk/v2'
import type { DaytonaV2Client } from '@daytona/sdk'
import { assertStarted, client, section, info, pass, fail, sleep, IMAGE, IMAGE_WAIT_SECONDS, RUN } from '../_common'

const DELETE_ATTEMPTS = 10
const DELETE_RETRY_SECONDS = Number(process.env.EXAMPLE_RETRY_DELAY_SECONDS ?? '3')

/** One budgeted wait on the image itself: it returns once the build settles. */
async function waitForBuild(daytona: DaytonaV2Client, image: Image): Promise<Image> {
  try {
    return await daytona.images.get(image.id, { wait: 'ready', timeoutSeconds: IMAGE_WAIT_SECONDS })
  } catch (error) {
    if (error instanceof RequestTimeoutError) throw new Error(`image ${image.name ?? image.id} did not settle within ${IMAGE_WAIT_SECONDS} s`)
    throw error
  }
}

/** The id of the image this run built: the build's reply, else a lookup by its unique name. */
async function builtImageId(daytona: DaytonaV2Client, built: Image | undefined, imageName: string): Promise<string | undefined> {
  if (built !== undefined) return built.id
  try {
    return (await daytona.images.get(imageName)).id
  } catch (error) {
    if (error instanceof NotFoundError) return undefined
    throw error
  }
}

/** Delete the image, retrying while the API still holds it for a deleted sandbox (409). */
async function deleteImage(daytona: DaytonaV2Client, imageId: string): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      await daytona.images.delete(imageId)
      return
    } catch (error) {
      if (!(error instanceof ConflictError) || attempt >= DELETE_ATTEMPTS) throw error
      info(`image ${imageId} still in use; retrying the delete`)
      await sleep(DELETE_RETRY_SECONDS)
    }
  }
}

async function main(): Promise<boolean> {
  const daytona = client()
  const imageName = `example-img-${RUN}`
  let built: Image | undefined

  try {
    section('images.build')
    const dockerfile = `FROM ${IMAGE}\nRUN pip install --no-cache-dir requests\n`
    built = await daytona.images.build(ImageBuilder.fromDockerfile(dockerfile), { name: imageName })
    info(`image status=${built.status} id=${built.id}`)

    section('images.get (wait for the build)')
    const image = await waitForBuild(daytona, built)
    if (image.status !== 'ready') {
      fail(`image ${imageName} ended ${image.status}${image.error ? `: ${image.error}` : ''}`)
      return false
    }
    pass('image is ready')

    section('create a sandbox from the built image')
    const sandbox = await daytona.create(imageName, { name: `from-img-${RUN}`, waitSeconds: 200 })
    try {
      assertStarted(sandbox)
      const py = await sandbox.process.run('python --version')
      info(`sandbox python: ${py.output.trim()}`)
      if (py.exitCode === 0) {
        pass('sandbox booted from the image and runs')
        return true
      }
      fail(`sandbox from image failed to run (exit=${py.exitCode})`)
      return false
    } finally {
      await daytona.delete(sandbox)
      info(`deleted sandbox ${sandbox.id}`)
    }
  } finally {
    section('cleanup image')
    const imageId = await builtImageId(daytona, built, imageName)
    if (imageId === undefined) info(`no image ${imageName} to delete`)
    else {
      await deleteImage(daytona, imageId)
      info(`deleted image ${imageId}`)
    }
  }
}

main()
  .then((ok) => process.exit(ok ? 0 : 1))
  .catch((error) => {
    console.error('Error:', error)
    process.exit(1)
  })
