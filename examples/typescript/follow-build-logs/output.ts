import type { Writable } from 'node:stream'

/** One CLI-lifetime writer: await each flushed chunk, not an unbounded queue. */
export function buildLogOutput(output: Writable, controller: AbortController) {
  let failure: NodeJS.ErrnoException | undefined
  const failed = (error: NodeJS.ErrnoException): void => {
    failure = error
    controller.abort(error)
  }
  // Keep this listener for the CLI lifetime: write callbacks run before error
  // events, including EPIPE when a downstream reader closes early.
  output.on('error', failed)
  return {
    get error(): NodeJS.ErrnoException | undefined { return failure },
    write(chunk: string): Promise<void> {
      return new Promise((resolve, reject) => {
        // Waiting for every write callback is stricter than waiting only when
        // write returns false: at most one log chunk can be queued at a time.
        output.write(chunk, error => {
          if (error) { failed(error); reject(error) }
          else resolve()
        })
      })
    },
  }
}
