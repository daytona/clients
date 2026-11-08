/** Follow an existing image or sandbox build without buffering its transcript.
 * Run: npx tsx typescript/follow-build-logs/index.ts image IMAGE_ID [CURSOR]
 *   or npx tsx typescript/follow-build-logs/index.ts sandbox SANDBOX_ID [CURSOR]
 * Uses DAYTONA_API_KEY (and DAYTONA_V2_API_URL in test environments). Requires live build-log support.
 */
import { Daytona } from '@daytona/sdk'
import { BuildLogInterruptedError } from '@daytona/sdk/v2'
import type { BuildLogEvent } from '@daytona/sdk/v2'
import { buildLogOutput } from './output.js'

async function main(): Promise<void> {
  const [kind, id, cursor] = process.argv.slice(2)
  if (!id || (kind !== 'image' && kind !== 'sandbox')) throw new Error('Expected image|sandbox ID [CURSOR]')
  const daytona = new Daytona().v2
  const controller = new AbortController()
  const output = buildLogOutput(process.stdout, controller)
  const interrupt = (): void => controller.abort()
  process.once('SIGINT', interrupt)
  const options = {
    cursor,
    signal: controller.signal,
    onLogs: (chunk: string): Promise<void> => output.write(chunk),
    onEvent: (event: BuildLogEvent): void => {
      if (['state', 'gap', 'unavailable', 'source_changed', 'error'].includes(event.type)) {
        process.stderr.write(`${JSON.stringify(event)}\n`)
      }
    },
  }
  try {
    const result = kind === 'image'
      ? await daytona.images.followBuildLogs(id, options)
      : await (await daytona.get(id)).followBuildLogs(options)
    process.stderr.write(`${JSON.stringify(result)}\n`)
  } catch (error) {
    // A consumer such as `head` intentionally stopped reading. End observation
    // quietly, without stopping the remote build or printing an EPIPE stack.
    if (output.error?.code === 'EPIPE') return
    if (!(error instanceof BuildLogInterruptedError)) throw error
    process.stderr.write(`${error.code}: ${error.message}; last checkpoint: ${error.cursor}\n`)
    process.exitCode = 1
  } finally {
    process.removeListener('SIGINT', interrupt)
  }
}

main().catch(error => { process.stderr.write(`${String(error)}\n`); process.exitCode = 1 })
