/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

import { io, Socket } from 'socket.io-client'

/** Handler receives (eventName, rawData). */
export type EventHandler = (eventName: string, data: any) => void

/**
 * Extracts the resource ID from an event payload.
 *
 * Handles two payload shapes:
 *   - Wrapper: {sandbox: {id: ...}, ...} -> nested resource ID
 *   - Direct: {id: ...} -> top-level ID
 */
function extractIdFromEvent(data: any): string | undefined {
  if (!data || typeof data !== 'object') return undefined
  for (const key of ['sandbox', 'volume', 'snapshot', 'runner']) {
    const nested = data[key]
    if (nested && typeof nested === 'object' && typeof nested.id === 'string') {
      return nested.id
    }
  }
  if (typeof data.id === 'string') {
    return data.id
  }
  return undefined
}

/**
 * Manages a Socket.IO connection to the Daytona notification gateway
 * and dispatches resource events to per-resource handlers.
 */
export class EventDispatcher {
  private socket: Socket | undefined
  private _connected = false
  private _closed = false
  private _failed = false
  private _failError: string | null = null
  private listeners = new Map<string, Set<EventHandler>>()
  private registeredEvents = new Set<string>()
  private connectPromise: Promise<void> | null = null
  private ensureConnectPromise: Promise<void> | null = null
  private reconnectAttempts = 0
  private readonly maxReconnectAttempts = 10
  // Cleared on connect/error/disconnect to prevent late state mutation.
  private _connectTimer: ReturnType<typeof setTimeout> | null = null
  private disconnectTimer: ReturnType<typeof setTimeout> | null = null
  private disconnectGeneration = 0
  private static readonly DISCONNECT_DELAY_MS = 30_000
  // Set once on the first Manager 'open' by capability probe, not runtime name.
  // `undefined` (pre-connect) is treated as `false` → ephemeral disconnect.
  private supportsBackgroundUnref: boolean | undefined = undefined

  constructor(
    private readonly apiUrl: string,
    private readonly token: string,
    private readonly organizationId?: string,
    private readonly source?: string,
    private readonly sdkVersion?: string,
  ) {}

  /**
   * Idempotent: ensure a connection attempt is in progress or already established.
   *
   * Non-blocking. Fires-and-forgets a connect() call via a stored promise if not
   * already connected and no attempt is currently running.
   */
  ensureConnected(): void {
    // No-op after disconnect — prevents socket resurrection.
    if (this._closed) return
    if (this._connected) return
    if (this.connectPromise) return
    if (this.ensureConnectPromise) return

    this.ensureConnectPromise = this.connect()
      .catch(() => {
        // Callers check isConnected when they need it
      })
      .finally(() => {
        this.ensureConnectPromise = null
      })
  }

  /**
   * Establishes the Socket.IO connection. Resolves when connected.
   * Throws if the connection fails within the timeout.
   */
  async connect(timeoutMs = 5000): Promise<void> {
    if (this._closed) {
      return
    }

    if (this._connected && this.socket) {
      return
    }

    if (this.connectPromise) {
      return this.connectPromise
    }

    this.connectPromise = this.doConnect(timeoutMs)

    try {
      await this.connectPromise

      if (this.listeners.size === 0) {
        this.scheduleDelayedDisconnect()
      }
    } catch (error) {
      if (this.listeners.size === 0) {
        // Stop retries if nobody is listening.
        this.socket?.disconnect()
      }
      throw error
    } finally {
      this.connectPromise = null
    }
  }

  private doConnect(timeoutMs: number): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      if (this.socket) {
        this.socket.removeAllListeners()
        this.socket.disconnect()
        this.socket = undefined
      }

      // Derive path from the API URL so reverse-proxy prefixes are preserved (not hardcoded /api).
      const url = new URL(this.apiUrl)
      const origin = url.origin
      const socketPath = `${url.pathname.replace(/\/$/, '')}/socket.io/`

      const query: Record<string, string> = {}
      if (this.organizationId) {
        query.organizationId = this.organizationId
      }
      if (this.source) {
        query.source = this.source
      }
      if (this.sdkVersion) {
        query.sdkVersion = this.sdkVersion
      }

      this.socket = io(origin, {
        path: socketPath,
        autoConnect: false,
        transports: ['websocket'],
        query,
        reconnection: true,
        reconnectionAttempts: this.maxReconnectAttempts,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 30000,
      })

      this.socket.auth = { token: this.token }

      this.clearConnectTimer()
      this._connectTimer = setTimeout(() => {
        if (!this._connected) {
          this.socket?.disconnect()
          this.clearConnectTimer()
          this._failed = true
          this._failError = 'WebSocket connection timed out'
          reject(new Error(this._failError))
        }
      }, timeoutMs)

      if (typeof this._connectTimer.unref === 'function') {
        this._connectTimer.unref()
      }

      this.socket.on('connect', () => {
        this.clearConnectTimer()
        this._connected = true
        this._failed = false
        this._failError = null
        this.reconnectAttempts = 0
        resolve()
      })

      this.socket.on('connect_error', (err) => {
        if (!this._connected) {
          this.clearConnectTimer()
          this._failed = true
          this._failError = `WebSocket connection failed: ${err.message}`
          reject(new Error(this._failError))
        }
      })

      this.socket.on('disconnect', (reason) => {
        this._connected = false
        if (reason === 'io server disconnect') {
          // Server initiated disconnect - try to reconnect
          this.socket?.connect()
        }
      })

      this.socket.io.on('reconnect', () => {
        this._connected = true
        this._failed = false
        this._failError = null
        this.reconnectAttempts = 0
      })

      this.socket.io.on('reconnect_attempt', () => {
        this.reconnectAttempts++
      })

      this.socket.io.on('reconnect_failed', () => {
        this._connected = false
        this._failed = true
        this._failError = `WebSocket reconnection failed after ${this.maxReconnectAttempts} attempts`
      })

      // Fires before engine.io's first _resetPingTimeout(), so enabling autoUnref here
      // also covers the initial + recurring ping timers and reconnect timers.
      this.socket.io.on('open', () => {
        this.applyUnrefCapability()
      })

      // Re-register any events that were added before the socket was created
      const pendingEvents = [...this.registeredEvents]
      this.registeredEvents.clear()
      this.registerEvents(pendingEvents)

      this.socket.connect()
    })
  }

  /**
   * Registers Socket.IO event handlers (idempotent -- each event is registered once).
   */
  private registerEvents(events: string[]): void {
    for (const eventName of events) {
      if (this.registeredEvents.has(eventName)) {
        continue
      }
      this.registeredEvents.add(eventName)

      // If socket isn't created yet, the event will be registered when connect() runs
      if (!this.socket) continue

      const handler = (data: any) => {
        const resourceId = extractIdFromEvent(data)
        if (resourceId) {
          this.dispatch(resourceId, eventName, data)
        }
      }

      this.socket.on(eventName, handler)
    }
  }

  /**
   * Registers a handler for events targeting a specific resource.
   * Returns an unsubscribe function.
   *
   * @param resourceId - The ID of the resource (e.g. sandbox ID).
   * @param handler - Callback receiving (eventName, rawData).
   * @param events - List of Socket.IO event names to listen for.
   */
  subscribe(resourceId: string, handler: EventHandler, events: string[]): () => void {
    // No-op after disconnect — prevents socket resurrection.
    if (this._closed) {
      return () => {
        return
      }
    }

    this.cancelDelayedDisconnect()
    this.disconnectGeneration++
    this.ensureConnected()

    if (!this.listeners.has(resourceId)) {
      this.listeners.set(resourceId, new Set())
    }
    this.listeners.get(resourceId)!.add(handler)

    // Register any new events with the Socket.IO client
    this.registerEvents(events)

    return () => {
      const handlers = this.listeners.get(resourceId)
      if (handlers) {
        handlers.delete(handler)
        if (handlers.size === 0) {
          this.listeners.delete(resourceId)
        }
      }

      // Schedule delayed disconnect when no resources are listening anymore
      if (this.listeners.size === 0) {
        this.scheduleDelayedDisconnect()
      }
    }
  }

  /** Whether the WebSocket is currently connected */
  get isConnected(): boolean {
    return this._connected
  }

  /** Whether the WebSocket has permanently failed (exhausted reconnection attempts) */
  get isFailed(): boolean {
    return this._failed
  }

  /** The error message if the connection has failed */
  get failError(): string | null {
    return this._failError
  }

  /** Disconnects and cleans up all resources */
  disconnect(): void {
    this._closed = true
    this.cancelDelayedDisconnect()
    this.clearConnectTimer()
    this.connectPromise = null
    this.ensureConnectPromise = null
    this.disconnectSocket()
  }

  private disconnectSocket(): void {
    if (this.socket) {
      this.socket.removeAllListeners()
      this.socket.disconnect()
      this.socket = undefined
    }
    this._connected = false
    this.listeners.clear()
    this.registeredEvents.clear()
  }

  /**
   * Decides, by capability rather than runtime name, whether this runtime can keep a
   * background socket open without blocking process exit.
   *
   * A runtime qualifies only if it can unref BOTH the transport's raw socket AND timers:
   * Node/Deno (ws package exposes `_socket`, timers are objects) qualify; Bun (native
   * WebSocket has no `_socket`) and browsers (timers are numbers, no `_socket`) do not.
   *
   * When it qualifies, this unrefs the current connection's socket (engine.io skipped it —
   * autoUnref was off during the transport's onopen) and enables engine.io's own autoUnref
   * so it keeps every later ping-timeout and reconnect timer unref'd. When it does not,
   * autoUnref stays off (avoiding the `ws._socket.unref()` and number-timer crashes) and
   * the socket is dropped the instant it goes idle (see scheduleDelayedDisconnect).
   */
  private applyUnrefCapability(): void {
    const manager = this.socket?.io as any
    // Known-unsupported runtime: nothing to unref; ephemeral disconnect handles exit.
    if (this.supportsBackgroundUnref === false) return
    // Re-apply per Manager: an idle disconnect replaces the socket with a fresh manager
    // whose autoUnref is off, so skip only when this manager already has it applied.
    if (this.supportsBackgroundUnref === true && manager?.opts?.autoUnref === true) return
    try {
      const engine = manager?.engine
      const rawSocket = engine?.transport?.ws?._socket
      const socketUnrefable = !!rawSocket && typeof rawSocket.unref === 'function'

      const probe = setTimeout(() => undefined, 0) as any
      const timerUnrefable = typeof probe?.unref === 'function'
      clearTimeout(probe)

      if (socketUnrefable && timerUnrefable) {
        rawSocket.unref()
        engine.opts.autoUnref = true
        manager.opts.autoUnref = true
        this.supportsBackgroundUnref = true
      } else if (this.supportsBackgroundUnref === undefined) {
        this.supportsBackgroundUnref = false
      }
    } catch {
      if (this.supportsBackgroundUnref === undefined) this.supportsBackgroundUnref = false
    }
  }

  private dispatch(resourceId: string, eventName: string, data: any): void {
    if (!resourceId) return
    const handlers = this.listeners.get(resourceId)
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(eventName, data)
        } catch {
          // Don't let a handler error break other handlers
        }
      }
    }
  }

  private cancelDelayedDisconnect(): void {
    if (this.disconnectTimer) {
      clearTimeout(this.disconnectTimer)
      this.disconnectTimer = null
    }
  }

  private clearConnectTimer(): void {
    if (this._connectTimer) {
      clearTimeout(this._connectTimer)
      this._connectTimer = null
    }
  }

  private scheduleDelayedDisconnect(): void {
    this.cancelDelayedDisconnect()
    const generation = this.disconnectGeneration

    // Where the socket can't be unref'd (Bun/browser), an open idle socket keeps the
    // event loop alive, so drop it immediately; elsewhere keep it warm for reuse.
    const delay = this.supportsBackgroundUnref === true ? EventDispatcher.DISCONNECT_DELAY_MS : 0

    this.disconnectTimer = setTimeout(() => {
      if (generation !== this.disconnectGeneration) {
        return
      }

      if (this.listeners.size === 0) {
        this.disconnectSocket()
      }
    }, delay)

    if (typeof this.disconnectTimer.unref === 'function') {
      this.disconnectTimer.unref()
    }
  }
}
