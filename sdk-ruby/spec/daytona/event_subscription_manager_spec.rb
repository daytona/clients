# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::EventSubscriptionManager do
  let(:fake_dispatcher_class) do
    Class.new do
      attr_reader :unsubscribed

      def initialize(failing_resources: [])
        @unsubscribed = []
        @failing_resources = failing_resources
        @mutex = Mutex.new
      end

      def subscribe(resource_id, events:, &_handler)
        _ = events
        proc do
          raise "unsubscribe failed for #{resource_id}" if @failing_resources.include?(resource_id)

          @mutex.synchronize { @unsubscribed << resource_id }
        end
      end
    end
  end

  let(:dispatcher) { fake_dispatcher_class.new }
  let(:handler) { proc { |_event_name, _data| } }
  let(:events) { ['sandbox.state.updated'] }

  def wait_until(timeout: 5)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
    sleep(0.01) while !yield && Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
    yield
  end

  describe '#subscribe' do
    it 'is a no-op when no dispatcher is configured' do
      manager = described_class.new(nil)

      sub_id = manager.subscribe(
        resource_id: 'sandbox-123',
        handler: proc { |_event_name, _data| },
        events: ['sandbox.state.updated']
      )

      expect(sub_id).to be_nil
      expect(manager.refresh('missing')).to be(false)
    end

    it 'shares one expiry thread across many subscriptions' do
      manager = described_class.new(dispatcher)
      threads_before = Thread.list.size

      sub_ids = Array.new(500) do |i|
        manager.subscribe(resource_id: "sandbox-#{i}", handler:, events:)
      end

      expect(sub_ids).to all(be_a(String))
      expect(Thread.list.size - threads_before).to be <= 1
      manager.shutdown
    end

    it 'expires the subscription after the TTL and works again after the worker went idle' do
      manager = described_class.new(dispatcher, ttl_seconds: 0.1)

      sub_id = manager.subscribe(resource_id: 'sandbox-1', handler:, events:)

      expect(wait_until { dispatcher.unsubscribed == ['sandbox-1'] }).to be(true)
      expect(manager.refresh(sub_id)).to be(false)

      manager.subscribe(resource_id: 'sandbox-2', handler:, events:)
      expect(wait_until { dispatcher.unsubscribed == %w[sandbox-1 sandbox-2] }).to be(true)
      manager.shutdown
    end
  end

  describe '#refresh' do
    it 'does not spawn threads' do
      manager = described_class.new(dispatcher)
      sub_id = manager.subscribe(resource_id: 'sandbox-1', handler:, events:)
      threads_before = Thread.list.size

      100.times { expect(manager.refresh(sub_id)).to be(true) }

      expect(Thread.list.size).to eq(threads_before)
      manager.shutdown
    end

    it 'extends the TTL past the original deadline' do
      manager = described_class.new(dispatcher, ttl_seconds: 0.3)
      sub_id = manager.subscribe(resource_id: 'sandbox-1', handler:, events:)

      4.times do
        sleep(0.15)
        expect(manager.refresh(sub_id)).to be(true)
      end
      expect(dispatcher.unsubscribed).to be_empty

      expect(wait_until { dispatcher.unsubscribed == ['sandbox-1'] }).to be(true)
      manager.shutdown
    end
  end

  describe '#unsubscribe' do
    it 'calls the dispatcher unsubscribe exactly once' do
      manager = described_class.new(dispatcher)
      sub_id = manager.subscribe(resource_id: 'sandbox-1', handler:, events:)

      manager.unsubscribe(sub_id)
      manager.unsubscribe(sub_id)

      expect(dispatcher.unsubscribed).to eq(['sandbox-1'])
      expect(manager.refresh(sub_id)).to be(false)
      manager.shutdown
    end
  end

  describe 'expiry worker robustness' do
    it 'keeps expiring other subscriptions when one dispatcher unsubscribe raises' do
      dispatcher = fake_dispatcher_class.new(failing_resources: ['sandbox-faulty'])
      manager = described_class.new(dispatcher, ttl_seconds: 0.1)

      manager.subscribe(resource_id: 'sandbox-faulty', handler:, events:)
      manager.subscribe(resource_id: 'sandbox-good', handler:, events:)

      expect(wait_until { dispatcher.unsubscribed.include?('sandbox-good') }).to be(true)
      manager.shutdown
    end
  end

  describe '#shutdown' do
    it 'unsubscribes everything and rejects new subscriptions' do
      manager = described_class.new(dispatcher)
      manager.subscribe(resource_id: 'sandbox-1', handler:, events:)
      manager.subscribe(resource_id: 'sandbox-2', handler:, events:)

      manager.shutdown

      expect(dispatcher.unsubscribed.sort).to eq(%w[sandbox-1 sandbox-2])
      expect(manager.subscribe(resource_id: 'sandbox-3', handler:, events:)).to be_nil
    end

    it 'stops the expiry worker' do
      manager = described_class.new(dispatcher, ttl_seconds: 60)
      manager.subscribe(resource_id: 'sandbox-1', handler:, events:)
      threads_with_worker = Thread.list.size

      manager.shutdown

      expect(wait_until { Thread.list.size == threads_with_worker - 1 }).to be(true)
    end
  end
end
