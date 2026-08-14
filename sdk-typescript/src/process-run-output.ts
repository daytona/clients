/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ProcessHandle } from './ProcessHandle'
import type { ProcessLogFrame } from '@daytona/toolbox-api-client'
import type { ProcessRunOptions } from './types/Process'
import { DaytonaError } from './errors/DaytonaError'
import { base64ToUint8Array } from './utils/Binary'

const MAX_LOG_PAGES = 10_000

export type CollectedRunOutput = {
  stdout: string
  stderr: string
  timedOut: boolean
}

// One incremental decoder per output side: the daemon ships raw byte chunks
// (base64) that can split a multibyte UTF-8 sequence across frames, so each
// side must decode as a continuous stream or split codepoints corrupt into
// U+FFFD. flush() drains any genuinely dangling trailing bytes at eof.
type ChannelDecoders = {
  stdout: TextDecoder
  stderr: TextDecoder
}

function newChannelDecoders(): ChannelDecoders {
  return { stdout: new TextDecoder('utf-8'), stderr: new TextDecoder('utf-8') }
}

function decodeFrameData(frame: ProcessLogFrame, decoder: TextDecoder): string {
  if (frame.encoding === 'base64') {
    return decoder.decode(base64ToUint8Array(frame.data), { stream: true })
  }
  return frame.data ?? ''
}

async function flushDecoders(
  decoders: ChannelDecoders,
  collected: CollectedRunOutput,
  onStdout?: ProcessRunOptions['onStdout'],
  onStderr?: ProcessRunOptions['onStderr'],
): Promise<void> {
  const stdoutTail = decoders.stdout.decode()
  if (stdoutTail) {
    collected.stdout += stdoutTail
    await onStdout?.(stdoutTail)
  }
  const stderrTail = decoders.stderr.decode()
  if (stderrTail) {
    collected.stderr += stderrTail
    await onStderr?.(stderrTail)
  }
}

async function dispatchFrame(
  frame: ProcessLogFrame,
  decoders: ChannelDecoders,
  collected: CollectedRunOutput,
  onStdout?: ProcessRunOptions['onStdout'],
  onStderr?: ProcessRunOptions['onStderr'],
): Promise<void> {
  // A pty merges stdout and stderr by construction, so its frames route to the
  // stdout side.
  if (frame.channel === 'stdout' || frame.channel === 'pty') {
    const data = decodeFrameData(frame, decoders.stdout)
    if (data === '') {
      return
    }
    collected.stdout += data
    await onStdout?.(data)
  } else if (frame.channel === 'stderr') {
    const data = decodeFrameData(frame, decoders.stderr)
    if (data === '') {
      return
    }
    collected.stderr += data
    await onStderr?.(data)
  }
}

/**
 * Collects a finished (or timed-out) process's retained output by paging the
 * ledger. Used by run() when no streaming callbacks were requested.
 *
 * The result is bounded by what the daemon still retains: output is capped by
 * the sandbox's log retention, so on eviction only the retained suffix comes
 * back and the page reports `truncatedHead`. To recover deliberately, read the
 * record's `firstAvailableCursor` (or the `warning` event on a live stream) and
 * page {@link ProcessHandle.logs} from there yourself.
 *
 * @throws {DaytonaError} If the ledger exceeds the page budget, rather than
 * returning silently partial output.
 */
export async function collectOutputFromLogs(handle: ProcessHandle): Promise<CollectedRunOutput> {
  const collected: CollectedRunOutput = { stdout: '', stderr: '', timedOut: false }
  const decoders = newChannelDecoders()
  let cursor = 'start'

  for (let page = 0; page < MAX_LOG_PAGES; page++) {
    const logs = await handle.logs({ cursor, limit: 1000, encoding: 'base64' })
    for (const frame of logs.frames) {
      await dispatchFrame(frame, decoders, collected)
    }
    if (logs.eof || logs.frames.length === 0) {
      await flushDecoders(decoders, collected)
      return collected
    }
    // A page that does not advance the cursor is drained even without eof:
    // re-requesting the same position would spend the entire page budget on
    // duplicate reads and then throw away everything already collected.
    if (!logs.nextCursor || logs.nextCursor === cursor) {
      await flushDecoders(decoders, collected)
      return collected
    }
    cursor = logs.nextCursor
  }

  throw new DaytonaError(
    `Process ${handle.id} has more retained output than the ${MAX_LOG_PAGES}-page collection budget. ` +
      'Read it incrementally with handle.logs({ cursor }) or handle.streamLogs() instead.',
  )
}

const STREAM_DEADLINE: unique symbol = Symbol('stream-deadline')

/**
 * Streams a process's output live, invoking onStdout/onStderr per frame while
 * accumulating, until eof or the deadline elapses. Deadline expiry closes the
 * stream and reports timedOut so run() can surface the same timed_out result a
 * plain wait() would.
 */
export async function streamOutputWithCallbacks(
  handle: ProcessHandle,
  onStdout: ProcessRunOptions['onStdout'],
  onStderr: ProcessRunOptions['onStderr'],
  deadlineMs?: number,
): Promise<CollectedRunOutput> {
  const collected: CollectedRunOutput = { stdout: '', stderr: '', timedOut: false }
  const deadline = deadlineMs !== undefined ? Date.now() + deadlineMs : undefined
  const decoders = newChannelDecoders()
  const iterator = handle.streamLogs({ cursor: 'start', encoding: 'base64' })[Symbol.asyncIterator]()

  try {
    for (;;) {
      let next: IteratorResult<Awaited<ReturnType<typeof iterator.next>>['value']> | typeof STREAM_DEADLINE
      if (deadline !== undefined) {
        const remaining = deadline - Date.now()
        if (remaining <= 0) {
          collected.timedOut = true
          break
        }
        const deadlineTimer = deadlineAfter(remaining)
        try {
          next = await Promise.race([iterator.next(), deadlineTimer.expired])
        } finally {
          deadlineTimer.cancel()
        }
        if (next === STREAM_DEADLINE) {
          collected.timedOut = true
          break
        }
      } else {
        next = await iterator.next()
      }

      if (next.done) {
        break
      }
      const event = next.value
      if (event.type === 'log') {
        await dispatchFrame(event.frame, decoders, collected, onStdout, onStderr)
      } else if (event.type === 'eof') {
        break
      }
    }
  } finally {
    await iterator.return?.(undefined)
  }
  await flushDecoders(decoders, collected, onStdout, onStderr)
  return collected
}

// The losing side of every race must be cancelled: an uncancelled timer keeps
// firing on the original deadline, pinning a Node.js event loop open and
// retaining one timer per frame on high-volume streams.
function deadlineAfter(ms: number): { expired: Promise<typeof STREAM_DEADLINE>; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined
  const expired = new Promise<typeof STREAM_DEADLINE>((resolve) => {
    timer = setTimeout(() => resolve(STREAM_DEADLINE), ms)
  })
  return {
    expired,
    cancel: () => {
      if (timer !== undefined) {
        clearTimeout(timer)
      }
    },
  }
}
