/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ProcessHandle } from './ProcessHandle'
import type { ProcessLogFrame } from '@daytona/toolbox-api-client'
import type { ProcessRunOptions } from './types/Process'

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
    return decoder.decode(Buffer.from(frame.data, 'base64'), { stream: true })
  }
  return frame.data
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
      break
    }
    cursor = logs.nextCursor
  }
  await flushDecoders(decoders, collected)
  return collected
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
        next = await Promise.race([iterator.next(), deadlineAfter(remaining)])
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

function deadlineAfter(ms: number): Promise<typeof STREAM_DEADLINE> {
  return new Promise((resolve) => setTimeout(() => resolve(STREAM_DEADLINE), ms))
}
