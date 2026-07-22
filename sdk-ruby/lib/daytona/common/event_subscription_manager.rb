# frozen_string_literal: true

# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

require 'securerandom'

module Daytona
  # Tracks dispatcher subscriptions with TTL auto-expiry.
  #
  # All subscriptions share ONE lazily started expiry worker thread instead of a
  # dedicated sleeping thread per subscription, so listing many sandboxes costs
  # one thread total, not one thread each
  # (https://github.com/daytona/clients/issues/108). #refresh only bumps the
  # subscription deadline — it never creates or kills threads.
  class EventSubscriptionManager
    SUBSCRIPTION_TTL = 300
    private_constant :SUBSCRIPTION_TTL

    def initialize(dispatcher = nil, ttl_seconds: SUBSCRIPTION_TTL)
      @dispatcher = dispatcher
      @ttl = ttl_seconds
      @subscriptions = {}
      # Deadline-ordered [expires_at, sub_id] entries. Entries are lazily
      # invalidated: a popped entry is discarded when its subscription is gone,
      # or re-queued at the current deadline when it was refreshed meanwhile.
      # Invariant: every live subscription has at least one queue entry.
      @expiry_queue = []
      @mutex = Mutex.new
      @cond = ConditionVariable.new
      @worker = nil
      @closed = false
    end

    def subscribe(resource_id:, handler:, events:)
      @mutex.synchronize do
        return nil if @closed || @dispatcher.nil?
      end

      unsubscribe = @dispatcher.subscribe(resource_id, events:, &handler)
      sub_id = SecureRandom.hex(16)
      rollback_unsubscribe = nil

      @mutex.synchronize do
        if @closed
          # Rollback dispatcher subscription on failure
          rollback_unsubscribe = unsubscribe
          next
        end

        expires_at = monotonic_now + @ttl
        @subscriptions[sub_id] = { unsubscribe:, expires_at: }
        push_entry_locked(expires_at, sub_id)
        ensure_worker_locked
        @cond.signal
      end

      if rollback_unsubscribe
        rollback_unsubscribe.call
        return nil
      end

      sub_id
    end

    def refresh(sub_id)
      @mutex.synchronize do
        return false if @closed

        subscription = @subscriptions[sub_id]
        return false unless subscription

        # No queue entry or worker wake-up needed: when the old deadline pops,
        # the worker sees the newer expires_at and re-queues the subscription.
        subscription[:expires_at] = monotonic_now + @ttl
        true
      end
    end

    def unsubscribe(sub_id)
      subscription = @mutex.synchronize do
        removed = @subscriptions.delete(sub_id)
        # The stale queue entry is discarded when the worker pops it — except
        # when it was the last subscription: drop the leftovers and wake the
        # worker so it exits now instead of idling until the old deadline.
        if removed && @subscriptions.empty?
          @expiry_queue.clear
          @cond.signal
        end
        removed
      end
      subscription&.dig(:unsubscribe)&.call
    end

    def shutdown
      subscriptions = nil

      @mutex.synchronize do
        @closed = true
        subscriptions = @subscriptions.values
        @subscriptions = {}
        @expiry_queue.clear
        @cond.broadcast
      end

      subscriptions.each { |subscription| subscription[:unsubscribe].call }
    end

    private

    def monotonic_now
      ::Process.clock_gettime(::Process::CLOCK_MONOTONIC)
    end

    # Caller must hold @mutex.
    def push_entry_locked(expires_at, sub_id)
      index = @expiry_queue.bsearch_index { |entry| entry[0] > expires_at } || @expiry_queue.length
      @expiry_queue.insert(index, [expires_at, sub_id])
    end

    # Caller must hold @mutex. The alive? check is a safety net: a worker that
    # crashed for any reason leaves a dead-but-truthy reference, and a plain nil
    # check would then block replacements forever, silently disabling expiry.
    def ensure_worker_locked
      return if @worker&.alive?

      @worker = Thread.new { expiry_loop }
      @worker.name = 'daytona-subscription-expiry'
      @worker.abort_on_exception = false
    end

    def expiry_loop
      loop do
        expired = collect_expired
        return if expired.nil?

        expired.each do |subscription|
          subscription[:unsubscribe].call
        rescue StandardError
          # One failing dispatcher unsubscribe must not kill the shared worker
          # and silently stop expiry for every other subscription.
          nil
        end
      end
    end

    # Blocks until at least one subscription expires. Returns nil when the
    # worker should exit (shutdown, or no live subscriptions remain).
    def collect_expired
      @mutex.synchronize do
        expired = []

        while expired.empty?
          return nil if @closed

          if @expiry_queue.empty?
            # No live subscriptions remain (see queue invariant above):
            # exit and let the next subscribe start a fresh worker.
            @worker = nil
            return nil
          end

          now = monotonic_now
          deadline = @expiry_queue.first[0]
          if deadline > now
            @cond.wait(@mutex, deadline - now)
            next
          end

          drain_due_entries_locked(now, expired)
        end

        expired
      end
    end

    # Caller must hold @mutex.
    def drain_due_entries_locked(now, expired)
      while !@expiry_queue.empty? && @expiry_queue.first[0] <= now
        _, sub_id = @expiry_queue.shift
        subscription = @subscriptions[sub_id]
        next unless subscription

        if subscription[:expires_at] > now
          push_entry_locked(subscription[:expires_at], sub_id)
          next
        end

        @subscriptions.delete(sub_id)
        expired << subscription
      end
    end
  end
end
