/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The axios adapter every SDK request goes through (internal): it enforces
 * the buffered-body cap (`maxResponseBytes`) before buffering, in every
 * runtime, and a file transfer's idle bound. See `boundedAdapter` below.
 */

import axios, { AxiosError } from 'axios'
import type { AxiosAdapter, AxiosRequestConfig, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { BodyTooLarge, IdleWatchdog, bytesAs, declaresMoreThan, discardBody, isSmallRequestBody, readCappedBody, tooLargeErrorStatusMessage, tooLargeMessage } from './bodyLimit.js'
import { PAYLOAD_TOO_LARGE_AXIOS_CODE, axiosResponseHeader, createDaytonaError, isJsonMediaType } from './errors/DaytonaError.js'
import { declaredLength } from './bodyBuffer.js'
import { boundedTimerDelay } from './options.js'

/**
 * An axios transfer is read whole by the adapter, which applies its idle
 * bound; no SDK call streams one (streamed transfers use `fetch`). Internal.
 */
function streamTransferError(): Error {
  return createDaytonaError('internal: an axios file transfer cannot use responseType "stream"; stream it through HttpTransport.fetch with transfer: true', 500, undefined, 'SDK')
}

/** The request meta the adapter reads (the transport's `daytona` field). */
interface TransferMeta {
  /** A file transfer: no total deadline, an idle bound instead. */
  readonly transfer?: boolean
  /** A transfer's idle bound, in ms (`0` = none); set when the request is armed. */
  readonly idleTimeoutMs?: number
}

function unrefTimer(timer: ReturnType<typeof setTimeout>): void {
  const unref = (timer as { unref?: () => void }).unref
  if (typeof unref === 'function') unref.call(timer)
}

/**
 * Mark an axios request as a file transfer: no total deadline, but it fails
 * with `ClientTimeoutError` once no bytes move for its own `timeout`, else
 * the instance's request timeout (connecting, the request body, the response
 * headers and every gap in the response body). `0` = no idle bound.
 */
export function transferRequest<T extends AxiosRequestConfig>(config: T): T {
  if (config.responseType === 'stream') throw streamTransferError()
  const own = config.timeout
  const marked = { ...config, timeout: 0 } as T & { daytona?: TransferMeta }
  marked.daytona = {
    ...marked.daytona,
    transfer: true,
    // The call's own timeout replaces the request timeout as its idle bound.
    ...(typeof own === 'number' ? { idleTimeoutMs: boundedIdleMs(own) } : {}),
  }
  return marked
}

/** A per-call idle bound in ms: `0`, or one above the longest timer, is none. */
function boundedIdleMs(timeoutMs: number): number {
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw createDaytonaError('timeout must be a non-negative finite number of milliseconds (0 = no limit)', 400, 'BAD_REQUEST', 'SDK')
  return boundedTimerDelay(timeoutMs)
}

/**
 * The axios `maxContentLength` for a per-call `maxResponseBytes` (already
 * validated): `{}` keeps the client's cap, `0` lifts it.
 */
export function maxContentLengthFor(maxResponseBytes: number | undefined): { readonly maxContentLength?: number } {
  if (maxResponseBytes === undefined) return {}
  return { maxContentLength: maxResponseBytes > 0 ? maxResponseBytes : -1 }
}

/** An axios adapter by name, when this runtime has it. */
function adapterNamed(name: 'http' | 'fetch'): AxiosAdapter | undefined {
  try {
    return axios.getAdapter(name)
  } catch {
    return undefined
  }
}

let httpAdapter: AxiosAdapter | null | undefined

/**
 * axios' Node http adapter where the runtime has one (Node, Bun, Deno), else
 * `null`: those runtimes go through {@link fetchAdapter}. Never axios' xhr
 * adapter (it reads a body whole before any size check) nor its fetch adapter
 * (some runtimes, workerd among them, fail its response-stream probe, and it
 * then buffers the body before checking its size).
 */
function nodeHttpAdapter(): AxiosAdapter | null {
  if (httpAdapter === undefined) httpAdapter = adapterNamed('http') ?? null
  return httpAdapter
}

/** Test seam: route requests through {@link fetchAdapter}, as in a runtime without Node's http module. */
export function forceFetchAdapterForTests(force: boolean): void {
  httpAdapter = force ? null : undefined
}

/**
 * The SDK's own fetch-based adapter, for runtimes without Node's http module:
 * the request axios has already serialized (URL with params, headers, body),
 * answered with the response body still unread (`data` is the web stream),
 * for {@link boundedAdapter} to read under its cap. `timeout` aborts like
 * axios' own (`ECONNABORTED`).
 */
async function fetchAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const url = axios.getUri(config)
  const headers = new Headers()
  const raw = (config.headers?.toJSON?.() ?? {}) as Record<string, unknown>
  for (const [name, value] of Object.entries(raw)) {
    if (value === undefined || value === null || value === false) continue
    headers.set(name, Array.isArray(value) ? value.join(', ') : String(value))
  }
  const method = (config.method ?? 'get').toUpperCase()
  const body = method === 'GET' || method === 'HEAD' ? undefined : config.data as BodyInit | undefined
  // fetch writes a FormData body's multipart boundary into Content-Type itself.
  if (typeof FormData !== 'undefined' && body instanceof FormData) headers.delete('content-type')
  const controller = new AbortController()
  const caller = config.signal as AbortSignal | undefined
  const relay = (): void => controller.abort(caller?.reason)
  if (caller?.aborted) relay()
  else caller?.addEventListener?.('abort', relay, { once: true })
  const timeoutMs = typeof config.timeout === 'number' ? boundedTimerDelay(config.timeout) : 0
  let timedOut = false
  const timer = timeoutMs > 0
    ? setTimeout(() => {
      timedOut = true
      controller.abort(new DOMException('timeout', 'AbortError'))
    }, timeoutMs)
    : undefined
  if (timer !== undefined) unrefTimer(timer)
  const done = (): void => {
    if (timer !== undefined) clearTimeout(timer)
    caller?.removeEventListener?.('abort', relay)
  }
  let response: Response
  try {
    response = await fetch(url, {
      ...(config.fetchOptions as RequestInit | undefined),
      method,
      headers,
      body,
      signal: controller.signal,
      credentials: config.withCredentials === true ? 'include' : 'omit',
      redirect: 'follow',
    })
  } catch (error) {
    done()
    if (timedOut) throw new AxiosError(`timeout of ${timeoutMs}ms exceeded`, AxiosError.ECONNABORTED, config)
    if (controller.signal.aborted) throw new AxiosError('canceled', AxiosError.ERR_CANCELED, config)
    throw new AxiosError(error instanceof Error ? error.message : String(error), AxiosError.ERR_NETWORK, config)
  }
  const responseHeaders: Record<string, string> = {}
  response.headers.forEach((value, name) => { responseHeaders[name] = value })
  const source = response.body?.getReader()
  // The body stays readable; the timer and the caller relay end with it.
  const data = source === undefined
    ? null
    : new ReadableStream<Uint8Array>({
      async pull(stream) {
        try {
          const next = await source.read()
          if (next.done) {
            done()
            stream.close()
          } else {
            stream.enqueue(next.value)
          }
        } catch (error) {
          done()
          stream.error(timedOut ? new AxiosError(`timeout of ${timeoutMs}ms exceeded`, AxiosError.ECONNABORTED, config) : error)
        }
      },
      async cancel(reason) {
        done()
        await source.cancel(reason).catch(() => undefined)
      },
    })
  if (data === null) done()
  return {
    data,
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
    config,
    request: undefined,
  }
}

function smallRequestBody(config: InternalAxiosRequestConfig): boolean {
  return isSmallRequestBody(config.method, config.data)
}

/** A JSON object error body, parsed; `undefined` for anything else. */
function errorEnvelopeData(bytes: Uint8Array): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes))
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : undefined
  } catch {
    return undefined
  }
}

function settleError(config: InternalAxiosRequestConfig, response: AxiosResponse): AxiosError {
  return new AxiosError(
    `Request failed with status code ${response.status}`,
    response.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST,
    config,
    response.request,
    response,
  )
}

/**
 * Every SDK axios request: the response is opened as a stream, so the body
 * cap (`maxContentLength`, the SDK's `maxResponseBytes`) is checked against a
 * declared `Content-Length` before the body is read and against the bytes as
 * they arrive, and a refused body is cancelled (its connection closed) rather
 * than buffered. A file transfer also gets its idle bound here: an abort once
 * no bytes move for its `idleTimeoutMs` (request body progress where the
 * runtime reports it, the response headers, every body chunk). Then the body
 * is handed on in the shape axios would have produced, and the caller's
 * `validateStatus` is applied as axios' own `settle` does.
 */
export async function boundedAdapter(config: InternalAxiosRequestConfig): Promise<AxiosResponse> {
  const httpBase = nodeHttpAdapter()
  const http = httpBase !== null
  const base: AxiosAdapter = httpBase ?? fetchAdapter
  const responseType = config.responseType
  if (responseType === 'stream') {
    // The idle bound is enforced on a body this adapter reads; a streamed
    // response is read by the caller, so it cannot be a transfer here.
    if ((config as InternalAxiosRequestConfig & { daytona?: TransferMeta }).daytona?.transfer === true) throw streamTransferError()
    if (http) return base(config)
    const streamed = await fetchAdapter(config)
    const accepts = config.validateStatus
    if (accepts === null || accepts === undefined || accepts(streamed.status)) return streamed
    // Nobody reads a refused stream: cancel it, so the reader releases the
    // connection and its timer (Node's http adapter frees its own).
    await discardBody(streamed.data)
    throw settleError(config, streamed)
  }
  const limit = typeof config.maxContentLength === 'number' && config.maxContentLength > 0 ? config.maxContentLength : 0
  const meta = (config as InternalAxiosRequestConfig & { daytona?: TransferMeta }).daytona
  const idleMs = meta?.transfer === true ? meta.idleTimeoutMs ?? 0 : 0
  const watchdog = idleMs > 0
    ? new IdleWatchdog(idleMs, config.signal as AbortSignal | undefined, () => new DOMException('no bytes moved', 'AbortError'))
    : undefined
  const request: InternalAxiosRequestConfig = {
    ...config,
    responseType: 'stream',
    maxContentLength: -1,
    validateStatus: () => true,
    signal: watchdog?.signal ?? config.signal,
  }
  if (watchdog !== undefined) {
    // Node's http adapter reports request body progress. fetch does not (a
    // streamed upload needs request streams, which some browsers refuse over
    // HTTP/1.1), so there a request with a larger body is bounded from its
    // response headers on; one with no body or a small one from the start.
    if (http) {
      const caller = config.onUploadProgress
      request.onUploadProgress = (event) => {
        watchdog.kick()
        caller?.(event)
      }
    }
    if (http || smallRequestBody(config)) watchdog.kick()
  }
  const idleTimeout = (): AxiosError => new AxiosError(`timeout of ${idleMs}ms exceeded with no bytes moving`, AxiosError.ECONNABORTED, config)
  const failed = (error: unknown): unknown => {
    if (watchdog?.fired()) return idleTimeout()
    if (config.signal?.aborted) return new AxiosError('canceled', AxiosError.ERR_CANCELED, config)
    if (error instanceof AxiosError) {
      // Keep the caller's config: a 401 retry re-sends it.
      error.config = config
      return error
    }
    return new AxiosError(error instanceof Error ? error.message : String(error), AxiosError.ERR_NETWORK, config)
  }
  try {
    let response: AxiosResponse
    try {
      response = await base(request)
    } catch (error) {
      throw failed(error)
    }
    const release = (): void => {
      const destroy = (response.request as { destroy?: () => void } | undefined)?.destroy
      if (typeof destroy === 'function') destroy.call(response.request)
    }
    const tooLarge = (): AxiosError => response.status >= 400
      ? new AxiosError(tooLargeErrorStatusMessage(response.status, limit), response.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST, config, response.request, { ...response, config, data: undefined })
      : new AxiosError(tooLargeMessage(limit), PAYLOAD_TOO_LARGE_AXIOS_CODE, config, response.request)
    let bytes: Uint8Array
    if (declaresMoreThan(response.headers['content-length'], limit)) {
      await discardBody(response.data, release)
      throw tooLarge()
    }
    try {
      bytes = await readCappedBody(response.data, limit, watchdog === undefined ? {} : { waiting: () => watchdog.kick(), received: () => watchdog.pause() }, release, declaredLength(response.headers['content-length']))
    } catch (error) {
      if (error instanceof BodyTooLarge) throw tooLarge()
      throw failed(error)
    }
    const settled: AxiosResponse = { ...response, config, data: bytesAs(bytes, responseType) }
    const validateStatus = config.validateStatus
    if (validateStatus === null || validateStatus === undefined || validateStatus(settled.status)) return settled
    // A refused status declared as application/json carries its error
    // envelope whatever `responseType` the success body would have had (a file
    // read asks for bytes), so the typed error keeps the server's code and
    // message; any other body is only the message.
    const envelope = isJsonMediaType(axiosResponseHeader(response.headers, 'content-type')) ? errorEnvelopeData(bytes) : undefined
    throw settleError(config, { ...settled, data: envelope ?? settled.data })
  } finally {
    watchdog?.dispose()
  }
}

