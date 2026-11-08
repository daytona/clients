/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport, sseMessages, strictUriEncode } from './http.js'
import type { KillOptions, LogsOptions, LogsPage, ProcessDto, ProcessHandleJSON, ProcessKind, StreamLogEvent, StreamLogsOptions, WaitResult } from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { ConnectionError, ClientTimeoutError, createDaytonaError } from './errors/DaytonaError.js'
import {
  base64Encode, frameFromWire, invalidPtyVerbError, invalidStdinVerbError, processFromWire,
  remainingWaitBudgetMs, signalWire, streamEvent, waitDeadline, waitResult,
  WAIT_READ_SLACK_MS, WAIT_TRANSPORT_RETRY_LIMIT,
} from './ProcessWire.js'
import type { LogFrameWire, LogsWire } from './ProcessWire.js'

interface WebSocketCtor {
  new (url: string, protocols?: string | string[]): WebSocketLike
}
interface WebSocketLike {
  binaryType: string
  readyState: number
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void
  close(code?: number, reason?: string): void
  addEventListener(type: 'open', listener: () => void): void
  addEventListener(type: 'message', listener: (event: { data: unknown }) => void): void
  addEventListener(type: 'error', listener: (event: unknown) => void): void
  addEventListener(type: 'close', listener: () => void): void
}
export interface WaitProcessOptions {
  /** Caller wait budget in milliseconds; `0` waits unbounded. */
  timeout?: number
}
export interface PtyDuplex {
  readable: AsyncIterable<Uint8Array>
  write(data: string | Uint8Array): void
  close(): void
}

export class ProcessHandle {
  public readonly id: string
  public readonly sandboxId: string
  public readonly kind?: ProcessKind

  constructor(
    private readonly transport: HttpTransport,
    sandboxId: string,
    processId: string,
    kind?: ProcessKind,
  ) {
    this.sandboxId = sandboxId
    this.id = processId
    this.kind = kind
  }

  public toJSON(): ProcessHandleJSON {
    return this.kind === undefined
      ? { sandboxId: this.sandboxId, processId: this.id }
      : { sandboxId: this.sandboxId, processId: this.id, kind: this.kind }
  }

  public async get(): Promise<ProcessDto> {
    return processFromWire(await this.transport.get<toolbox.ProcessDto>(`/processes/${strictUriEncode(this.id)}`))
  }

  public async logs(options: LogsOptions = {}): Promise<LogsPage> {
    const query = encodeQuery({ cursor: options.cursor, limit: options.limit, encoding: options.encoding })
    const data = await this.transport.get<LogsWire | LogFrameWire[]>(`/processes/${strictUriEncode(this.id)}/logs${query}`)
    if (Array.isArray(data)) return { items: data.map(frameFromWire) }
    const items = 'items' in data ? (data.items ?? []) : ('frames' in data ? data.frames : [])
    return {
      items: items.map(frameFromWire),
      nextCursor: 'nextCursor' in data ? data.nextCursor : undefined,
      truncatedHead: 'truncatedHead' in data ? data.truncatedHead : undefined,
      firstAvailableCursor: 'firstAvailableCursor' in data ? data.firstAvailableCursor : undefined,
    }
  }

  public async *streamLogs(options: StreamLogsOptions = {}): AsyncIterable<StreamLogEvent> {
    const query = encodeQuery({ cursor: options.cursor, limit: options.limit, encoding: options.encoding, follow: true })
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (options.cursor) headers['Last-Event-ID'] = options.cursor
    const response = await this.transport.fetch(`/processes/${strictUriEncode(this.id)}/logs${query}`, { headers, signal: options.signal })
    for await (const message of sseMessages(response)) yield streamEvent(message)
  }

  /** Write to the stdin of an exec or pty process created with stdin='open'. */
  public async writeStdin(data: string | Uint8Array): Promise<void> {
    if (this.kind === 'code' || this.kind === 'session') throw invalidStdinVerbError('writeStdin', this.kind)
    const body: toolbox.StdinRequest = typeof data === 'string'
      ? { data }
      : { data: base64Encode(data), encoding: 'base64' }
    await this.transport.post<void>(`/processes/${strictUriEncode(this.id)}/stdin`, body)
  }

  /** Close the stdin of an exec or pty process (EOF). */
  public async closeStdin(): Promise<void> {
    if (this.kind === 'code' || this.kind === 'session') throw invalidStdinVerbError('closeStdin', this.kind)
    await this.transport.post<void>(`/processes/${strictUriEncode(this.id)}/stdin`, { data: '', eof: true })
  }

  public async wait(options: WaitProcessOptions = {}): Promise<WaitResult> {
    const deadline = waitDeadline(options.timeout)
    let consecutiveTransportFailures = 0
    for (;;) {
      const requestTimeout = deadline === undefined ? 0 : remainingWaitBudgetMs(deadline)
      if (deadline !== undefined && requestTimeout <= 0) return { reason: 'timed_out' }
      try {
        const data = await this.transport.get<toolbox.ExitResult>(
          `/processes/${strictUriEncode(this.id)}/wait${encodeQuery({ timeoutMs: requestTimeout })}`,
          { timeout: deadline === undefined ? 0 : requestTimeout + WAIT_READ_SLACK_MS },
        )
        // One retried unit: the long-poll plus, on `timed_out`, the record
        // read; the failure streak resets only when both succeed.
        const result = waitResult(data)
        if (result.reason !== 'timed_out') return result
        if (deadline !== undefined && remainingWaitBudgetMs(deadline) <= 0) return { reason: 'timed_out' }
        // Budget left: `timed_out` is either an expired long-poll or a command
        // its own deadline stopped, and the record says which.
        const record = await this.transport.get<toolbox.ProcessDto>(
          `/processes/${strictUriEncode(this.id)}`,
          deadline === undefined ? undefined : { timeout: remainingWaitBudgetMs(deadline) + WAIT_READ_SLACK_MS },
        )
        consecutiveTransportFailures = 0
        if (record.state === 'terminal') return waitResult(record)
      } catch (error) {
        if (!(error instanceof ConnectionError || error instanceof ClientTimeoutError)) throw error
        consecutiveTransportFailures += 1
        if (deadline !== undefined && remainingWaitBudgetMs(deadline) <= 0) return { reason: 'timed_out' }
        if (consecutiveTransportFailures > WAIT_TRANSPORT_RETRY_LIMIT) throw error
      }
    }
  }

  public async kill(options: KillOptions = {}): Promise<void> {
    const body: toolbox.SignalRequest = {
      signal: signalWire(options.signal),
      escalateAfterMs: options.escalateAfter,
      escalateTo: signalWire(options.escalateTo),
    }
    await this.transport.post<void>(`/processes/${strictUriEncode(this.id)}/signals`, body)
  }

  public async resize(cols: number, rows: number): Promise<void> {
    if (this.kind !== undefined && this.kind !== 'pty') throw invalidPtyVerbError('resize', this.kind)
    await this.transport.post<void>(`/processes/${strictUriEncode(this.id)}/resize`, { cols, rows })
  }

  /**
   * Delete the process and its logs, stopping it first if it is running.
   * Refused (`UnsupportedOperationError`) for a running code cell, and for a
   * session command that has not stopped within 5 s: delete its session.
   */
  public async delete(): Promise<void> {
    await this.transport.delete<void>(`/processes/${strictUriEncode(this.id)}`)
  }

  public async attach(): Promise<PtyDuplex> {
    if (this.kind !== undefined && this.kind !== 'pty') throw invalidPtyVerbError('attach', this.kind)
    const ctor = (globalThis as { WebSocket?: WebSocketCtor }).WebSocket
    if (!ctor) throw createDaytonaError('WebSocket is not available in this runtime', 400, 'UNSUPPORTED_OPERATION', 'SDK')
    const url = new URL(`${this.transport.baseUrl}/processes/${strictUriEncode(this.id)}/attach`)
    const token = await this.transport.resolveToken()
    if (token) url.searchParams.set('daytona_token', token)
    const wsUrl = url.toString().replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')
    const socket = new ctor(wsUrl)
    socket.binaryType = 'arraybuffer'
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener('open', resolve)
      socket.addEventListener('error', () => reject(createDaytonaError('WebSocket attach failed', undefined, undefined, 'SDK')))
    })
    const queue: Uint8Array[] = []
    const waiters: Array<(value: IteratorResult<Uint8Array>) => void> = []
    let closed = false
    socket.addEventListener('message', (event) => {
      const bytes = bytesFromMessage(event.data)
      const waiter = waiters.shift()
      if (waiter) waiter({ value: bytes, done: false })
      else queue.push(bytes)
    })
    socket.addEventListener('close', () => {
      closed = true
      for (const waiter of waiters.splice(0)) waiter({ value: new Uint8Array(), done: true })
    })
    return {
      readable: {
        [Symbol.asyncIterator](): AsyncIterator<Uint8Array> {
          return {
            next: () => {
              const value = queue.shift()
              if (value) return Promise.resolve({ value, done: false })
              if (closed) return Promise.resolve({ value: new Uint8Array(), done: true })
              return new Promise((resolve) => waiters.push(resolve))
            },
          }
        },
      },
      write: (data) => socket.send(typeof data === 'string' ? data : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)),
      close: () => socket.close(),
    }
  }
}

function bytesFromMessage(data: unknown): Uint8Array {
  if (typeof data === 'string') return new TextEncoder().encode(data)
  if (data instanceof ArrayBuffer) return new Uint8Array(data)
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  return new Uint8Array()
}
