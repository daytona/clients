# frozen_string_literal: true

# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

require_relative 'socketio_client'

module Daytona
  # Manages a Socket.IO connection and dispatches events to per-resource handlers.
  # Generic — works for sandboxes, volumes, snapshots, runners, etc.
  #
  # The connection opens lazily with the first subscription and closes DISCONNECT_DELAY seconds
  # after the last one goes away. The server is told which resource ids have listeners
  # (subscribe / unsubscribe events), so it only sends events for those. Older servers ignore
  # this and send every organization event; incoming events are always filtered by id.
  class EventDispatcher
    DISCONNECT_DELAY = 30

    # Most resource ids per subscribe / unsubscribe message.
    SUBSCRIBE_BATCH_SIZE = 100

    # Most resource ids one connection may subscribe to; the gateway's wire contract rejects a subscribe
    # beyond it whole. Specs lower it with stub_const.
    MAX_SUBSCRIPTIONS_PER_CONNECTION = 1000

    # @param api_url [String]
    # @param token [String]
    # @param organization_id [String, nil]
    def initialize(api_url:, token:, organization_id: nil, source: nil, sdk_version: nil)
      @api_url = api_url
      @token = token
      @organization_id = organization_id
      @source = source
      @sdk_version = sdk_version
      @client = nil
      @connected = false
      # Successful connects so far, reconnects included. Events emitted while disconnected are lost, so a
      # changed epoch means a gap. Guarded by @mutex.
      @connection_epoch = 0
      @failed = false
      @fail_error = nil
      @listeners = {}
      # The ids the server serves, or will after the next connect: at most MAX_SUBSCRIPTIONS_PER_CONNECTION of
      # the ids with listeners. An id that found no room stays unserved until its last listener goes.
      @served = Set.new
      @registered_events = Set.new
      @mutex = Mutex.new
      @disconnect_timer = nil
      @disconnect_generation = 0
      @reconnect_thread = nil
      @connect_thread = nil
      @reconnecting = false
      @connecting = false
      @close_requested = false
      @closed = false
      @max_reconnects = 100
    end

    # Idempotent: ensure a connection attempt is in progress or already established.
    # Non-blocking. Starts a background Thread to connect if not already connected
    # and no attempt is currently running.
    # @return [void]
    def ensure_connected
      @mutex.synchronize do
        return if @closed || @connected || @connecting || @connect_thread&.alive?

        @connect_thread = Thread.new do
          connect
        rescue StandardError
          # Callers check connected? when they need it
        ensure
          @mutex.synchronize do
            @connect_thread = nil if @connect_thread == Thread.current
          end
        end
      end
    end

    # Establish the Socket.IO connection.
    # @return [void]
    # @raise [StandardError] on connection failure
    def connect
      @mutex.synchronize do
        return if @closed || @connected || @connecting

        @connecting = true
      end

      # Close any existing stale connection before creating a fresh one
      @client&.close rescue nil # rubocop:disable Style/RescueModifier

      client = SocketIOClient.new(
        api_url: @api_url,
        token: @token,
        organization_id: @organization_id,
        source: @source,
        sdk_version: @sdk_version,
        on_event: method(:handle_event),
        on_disconnect: method(:handle_disconnect)
      )

      @mutex.synchronize do
        @client = client
        @close_requested = false
      end

      client.connect

      @mutex.synchronize do
        @connected = true
        @connection_epoch += 1
        @failed = false
        @fail_error = nil
        # Subscriptions belong to the socket: send the whole set after every (re)connect.
        emit_ids_locked('subscribe', @served.to_a)
        schedule_delayed_disconnect_locked if @listeners.empty?
      end
    rescue StandardError => e
      @mutex.synchronize do
        @failed = true
        @fail_error = "WebSocket connection failed: #{e.message}"
      end
    ensure
      @mutex.synchronize { @connecting = false }
    end

    # Subscribe to specific events for a resource.
    # @param resource_id [String] The ID of the resource (e.g. sandbox ID, volume ID).
    # @param events [Array<String>] List of Socket.IO event names to listen for.
    # @yield [event_name, data] Called with raw event name and data hash.
    # @return [Proc] Unsubscribe function.
    def subscribe(resource_id, events:, &handler)
      @mutex.synchronize do
        # No-op after disconnect
        return -> {} if @closed

        @disconnect_timer&.kill
        @disconnect_timer = nil
        @disconnect_generation += 1

        first_listener = !@listeners.key?(resource_id)
        @listeners[resource_id] ||= []
        @listeners[resource_id] << handler
        # Mark events in filter under lock to avoid dropping first event
        events.each { |evt| @registered_events.add(evt) }
        serve_locked(resource_id) if first_listener
      end

      ensure_connected

      removed = false
      lambda {
        @mutex.synchronize do
          next if removed || @closed

          removed = true
          remove_listener_locked(resource_id, handler)
          schedule_delayed_disconnect_locked if @listeners.empty?
        end
      }
    end

    # @return [Boolean]
    def connected?
      @connected
    end

    # @param resource_id [String]
    # @return [Boolean] Whether the server is asked to send events for +resource_id+: it has listeners and
    #   was within the per-connection cap when it got its first
    def served?(resource_id)
      @mutex.synchronize { @served.include?(resource_id) }
    end

    # @return [Integer] The number of successful connects so far
    def connection_epoch
      @mutex.synchronize { @connection_epoch }
    end

    # @return [Boolean]
    def failed?
      @failed
    end

    # @return [String, nil]
    attr_reader :fail_error

    # Disconnect and clean up.
    def disconnect
      thread_state = @mutex.synchronize do
        begin_disconnect_locked(permanent: true, skip_thread: Thread.current)
      end

      finalize_disconnect(thread_state)
    end

    private

    # Removes this exact handler once (handlers can compare equal, e.g. two Method objects of the same
    # receiver), and tells the server when the resource loses its last listener.
    def remove_listener_locked(resource_id, handler)
      handlers = @listeners[resource_id]
      return unless handlers

      index = handlers.index { |h| h.equal?(handler) }
      handlers.delete_at(index) if index
      return unless handlers.empty?

      @listeners.delete(resource_id)
      emit_ids_locked('unsubscribe', [resource_id]) if @served.delete?(resource_id) && @connected
    end

    # Serves an id that gained its first listener, if the cap has room. While disconnected only the set
    # changes; the next connect sends it. Beyond the cap nothing is sent: the server would reject it, and
    # the id's waits poll instead.
    def serve_locked(resource_id)
      return if @served.size >= MAX_SUBSCRIPTIONS_PER_CONNECTION

      @served.add(resource_id)
      emit_ids_locked('subscribe', [resource_id]) if @connected
    end

    # Fire-and-forget; sent under the lock so subscribe / unsubscribe messages keep their order.
    def emit_ids_locked(event_name, ids)
      return if ids.empty? || @client.nil?

      ids.each_slice(SUBSCRIBE_BATCH_SIZE) do |batch|
        @client.emit(event_name, { sandboxIds: batch })
      end
    rescue StandardError
      nil
    end

    def schedule_delayed_disconnect_locked
      @disconnect_timer&.kill
      generation = @disconnect_generation
      @disconnect_timer = Thread.new do
        sleep(DISCONNECT_DELAY)
        thread_state = @mutex.synchronize do
          next unless generation == @disconnect_generation
          next unless @listeners.empty?

          begin_disconnect_locked(permanent: false, skip_thread: Thread.current)
        end

        finalize_disconnect(thread_state) if thread_state
      end
      @disconnect_timer.abort_on_exception = false
    end

    def handle_event(event_name, data)
      # Read registered_events under lock for thread safety
      registered = @mutex.synchronize { @registered_events.include?(event_name) }
      return unless registered

      resource_id = extract_id_from_event(data)
      return unless resource_id

      dispatch(resource_id, event_name, data)
    end

    # Extract resource ID from an event payload.
    # Handles two payload shapes:
    #   - Wrapper: {sandbox: {id: ...}, ...} -> nested resource ID
    #   - Direct: {id: ...} -> top-level ID
    def extract_id_from_event(data)
      return nil unless data.is_a?(Hash)

      %w[sandbox volume snapshot runner].each do |key|
        nested = data[key]
        next unless nested.is_a?(Hash)

        sid = nested['id']
        return sid if sid.is_a?(String)
      end

      top_id = data['id']
      return top_id if top_id.is_a?(String)

      nil
    end

    def dispatch(resource_id, event_name, data)
      handlers = @mutex.synchronize { @listeners[resource_id]&.dup || [] }
      handlers.each do |handler|
        handler.call(event_name, data)
      rescue StandardError
        # Don't let handler errors break other handlers
      end
    end

    def handle_disconnect
      @connected = false
      return if @close_requested

      @reconnect_thread = Thread.new { reconnect_loop }
    end

    def reconnect_loop
      @mutex.synchronize do
        return if @reconnecting

        @reconnecting = true
      end

      attempt = 0
      while attempt < @max_reconnects
        if @close_requested
          @mutex.synchronize { @reconnecting = false }
          return
        end

        delay = [2**attempt, 30].min
        sleep(delay)
        if @close_requested
          @mutex.synchronize { @reconnecting = false }
          return
        end

        begin
          connect
          if @connected
            @mutex.synchronize { @reconnecting = false }
            return
          end
        rescue StandardError
          # Continue retrying
        end

        attempt += 1
      end

      # All attempts failed
      @mutex.synchronize do
        @failed = true
        @fail_error = "WebSocket reconnection failed after #{@max_reconnects} attempts"
        @reconnecting = false
      end
    end

    def begin_disconnect_locked(permanent:, skip_thread:)
      @closed = true if permanent
      @close_requested = true

      thread_state = {
        client: @client,
        reconnect_thread: @reconnect_thread,
        connect_thread: @connect_thread,
        disconnect_timer: @disconnect_timer,
        skip_thread:
      }

      @client = nil
      @reconnect_thread = nil
      @connect_thread = nil
      @disconnect_timer = nil
      @connected = false
      @connecting = false
      @listeners.clear
      @served.clear
      @registered_events.clear

      thread_state
    end

    def finalize_disconnect(thread_state)
      return unless thread_state

      [thread_state[:reconnect_thread], thread_state[:connect_thread], thread_state[:disconnect_timer]].each do |thread|
        next unless thread
        next if thread == thread_state[:skip_thread]

        stop_thread(thread)
      end

      thread_state[:client]&.close
    rescue StandardError
      nil
    end

    def stop_thread(thread)
      return unless thread
      return if thread == Thread.current

      thread.kill
      thread.join
    rescue StandardError
      nil
    end
  end
end
