/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { encodeQuery, HttpTransport, sseMessages, strictUriEncode } from './http.js'
import type {
  KillOptions, LogPage, LogsOptions, ProcessHandleJSON, ProcessInfo, ProcessKind, StreamLogEvent, StreamLogsOptions,
  WaitProcessOptions, WaitResult,
} from './types.js'
import type * as toolbox from './gen/toolbox-types.js'
import { ConnectionError, ClientTimeoutError, UnsupportedOperationError } from './errors/DaytonaError.js'
import {
  base64Encode, frameFromWire, invalidPtyVerbError, invalidStdinVerbError, processFromWire,
  remainingWaitBudgetMs, exactMs, signalWire, streamEvent, waitDeadline, waitResult,
  WAIT_READ_SLACK_MS, WAIT_TRANSPORT_RETRY_LIMIT,
} from './ProcessWire.js'
import type { LogFrameWire, LogsWire } from './ProcessWire.js'
import { assertInternal } from './internal.js'
import { logPageFromWire } from './toolboxModels.js'
import type { Internal } from './internal.js'
import { assertKnownOptions, optionKeys } from './options.js'

const LOGS_OPTION_KEYS = optionKeys<LogsOptions>()({ cursor: true, limit: true, encoding: true })
const STREAM_LOGS_OPTION_KEYS = optionKeys<StreamLogsOptions>()({ cursor: true, limit: true, encoding: true, signal: true })
const WAIT_OPTION_KEYS = optionKeys<WaitProcessOptions>()({ timeoutSeconds: true })
const KILL_OPTION_KEYS = optionKeys<KillOptions>()({ signal: true, escalateAfterSeconds: true, escalateTo: true })

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
/** A live connection to a PTY's terminal (`handle.attach()`). `close()` disconnects; it does not stop the process. */
export interface PtyDuplex {
  readonly readable: AsyncIterable<Uint8Array>
  write(data: string | Uint8Array): void
  close(): void
}

/**
 * Handle for one sandbox process. Returned by `process.start()` /
 * `process.connect()` and the PTY and session namespaces, never constructed
 * directly. `toJSON()` gives an identity `sandbox.process.connect()` accepts
 * on any worker.
 */
export class ProcessHandle {
  public readonly id: string
  public readonly sandboxId: string
  public readonly kind?: ProcessKind
  private readonly transport: HttpTransport

  /** @internal */
  constructor(token: Internal, transport: HttpTransport, sandboxId: string, processId: string, kind?: ProcessKind)
  /** Built by the SDK: get it from `sandbox.process.start()` or `connect()`. */
  constructor(token: Internal, ...init: never[])
  constructor(token: Internal, transport: HttpTransport, sandboxId: string, processId: string, kind?: ProcessKind) {
    assertInternal(token, 'ProcessHandle')
    this.transport = transport
    this.sandboxId = sandboxId
    this.id = processId
    if (kind !== undefined) this.kind = kind
  }

  /** The handle's JSON-safe identity, for `sandbox.process.connect()`. */
  public toJSON(): ProcessHandleJSON {
    return this.kind === undefined
      ? { sandboxId: this.sandboxId, processId: this.id }
      : { sandboxId: this.sandboxId, processId: this.id, kind: this.kind }
  }

  /** Fetch current process metadata. */
  public async get(): Promise<ProcessInfo> {
    return processFromWire(await this.transport.get<toolbox.ProcessDto>(`/processes/${strictUriEncode(this.id)}`))
  }

  /** Read one page of the process log, from `cursor`. */
  public async logs(options: LogsOptions = {}): Promise<LogPage> {
    assertKnownOptions('LogsOptions', options, LOGS_OPTION_KEYS)
    const query = encodeQuery({ cursor: options.cursor, limit: options.limit, encoding: options.encoding })
    const data = await this.transport.get<LogsWire>(`/processes/${strictUriEncode(this.id)}/logs${query}`)
    return logPageFromWire(data)
  }

  /**
   * Follow the process log as it is written, replaying from `cursor`. Yields
   * one {@link StreamLogEvent} per server event: `log` (a frame), `state`
   * (the process became terminal), `warning` (frames before
   * `firstAvailableCursor` were evicted) and `eof`. A stream error throws the
   * typed error for its code; a stream cut short throws `ConnectionError`. An
   * iteration that ends without an `eof` event did not reach the end of the
   * log: resume from the last event's cursor.
   */
  public async *streamLogs(options: StreamLogsOptions = {}): AsyncIterable<StreamLogEvent> {
    assertKnownOptions('StreamLogsOptions', options, STREAM_LOGS_OPTION_KEYS)
    const query = encodeQuery({ cursor: options.cursor, limit: options.limit, encoding: options.encoding, follow: true })
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (options.cursor) headers['Last-Event-ID'] = options.cursor
    const response = await this.transport.fetch(`/processes/${strictUriEncode(this.id)}/logs${query}`, { headers, signal: options.signal })
    for await (const message of sseMessages(response)) {
      const event = streamEvent(message)
      if (event === undefined) continue
      yield event
      // The log is complete: nothing after eof is read (leaving the loop
      // cancels the body).
      if (event.type === 'eof') return
    }
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

  /**
   * Wait until the process ends. `timeoutSeconds` is the caller's wait budget
   * (omitted or `0` waits until it ends); on expiry the result is
   * `{ reason: 'timed_out' }` and the process keeps running.
   */
  public async wait(options: WaitProcessOptions = {}): Promise<WaitResult> {
    assertKnownOptions('WaitProcessOptions', options, WAIT_OPTION_KEYS)
    const deadline = waitDeadline(options.timeoutSeconds)
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

  /** Send a signal (default `TERM`), optionally escalating to `escalateTo` after `escalateAfterSeconds`. */
  public async kill(options: KillOptions = {}): Promise<void> {
    assertKnownOptions('KillOptions', options, KILL_OPTION_KEYS)
    const escalateAfterMs = exactMs('escalateAfterSeconds', options.escalateAfterSeconds)
    const body: toolbox.SignalRequest = {
      signal: signalWire(options.signal ?? 'TERM'),
      ...(escalateAfterMs === undefined ? {} : { escalateAfterMs, escalateTo: signalWire(options.escalateTo ?? 'KILL') }),
    }
    await this.transport.post<void>(`/processes/${strictUriEncode(this.id)}/signals`, body)
  }

  /** Resize a PTY. */
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

  /** Connect to a PTY's terminal. */
  public async attach(): Promise<PtyDuplex> {
    if (this.kind !== undefined && this.kind !== 'pty') throw invalidPtyVerbError('attach', this.kind)
    const ctor = (globalThis as { WebSocket?: WebSocketCtor }).WebSocket
    if (!ctor) throw new UnsupportedOperationError('WebSocket is not available in this runtime', { statusCode: 400, code: 'UNSUPPORTED_OPERATION', source: 'SDK' })
    const url = new URL(`${this.transport.baseUrl}/processes/${strictUriEncode(this.id)}/attach`)
    const token = await this.transport.resolveToken()
    if (token) url.searchParams.set('daytona_token', token)
    const wsUrl = url.toString().replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')
    const socket = new ctor(wsUrl)
    socket.binaryType = 'arraybuffer'
    await new Promise<void>((resolve, reject) => {
      let opened = false
      socket.addEventListener('open', () => {
        opened = true
        resolve()
      })
      socket.addEventListener('error', () => {
        if (opened) return
        socket.close()
        reject(new ConnectionError('WebSocket attach failed'))
      })
      // A socket that closes before it opens must not leave attach() pending.
      socket.addEventListener('close', () => {
        if (!opened) reject(new ConnectionError('WebSocket closed before the attach completed'))
      })
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
