# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::EventDispatcher do
  # Stands in for SocketIOClient: records emitted events; #connect can be held until released.
  let(:fake_client_class) do
    Class.new do
      attr_reader :options, :emitted

      def initialize(options, gate: nil, failing_emit: false)
        @options = options
        @gate = gate
        @failing_emit = failing_emit
        @emitted = []
        @mutex = Mutex.new
      end

      def connect
        @gate&.pop
      end

      def emit(event_name, data)
        raise IOError, 'socket closed' if @failing_emit

        @mutex.synchronize { @emitted << [event_name, data] }
      end

      # Records like #emit; #acknowledge then answers the nth acknowledged event.
      def emit_with_ack(event_name, data, &on_ack)
        emit(event_name, data)
        @mutex.synchronize { (@acks ||= []) << on_ack }
      end

      def acknowledge(index, ack)
        @mutex.synchronize { @acks[index] }.call(ack)
      end

      def emitted_events
        @mutex.synchronize { @emitted.dup }
      end

      def close; end
    end
  end

  let(:clients) { [] }
  let(:gate) { nil }
  let(:failing_emit) { false }
  let(:dispatcher) { described_class.new(api_url: 'https://api.example.com/api', token: 'token') }
  let(:events) { ['sandbox.state.updated'] }
  let(:noop) { proc { |_event_name, _data| } }

  before do
    allow(Daytona::SocketIOClient).to receive(:new) do |**options|
      fake_client_class.new(options, gate:, failing_emit:).tap { |client| clients << client }
    end
    # Reconnect without the backoff delay; the 30 s disconnect linger keeps its real sleep.
    allow(dispatcher).to receive(:sleep).and_call_original
    allow(dispatcher).to receive(:sleep).with(1)
  end

  after { dispatcher.disconnect }

  def wait_until(timeout: 5)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
    sleep(0.01) while !yield && Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
    yield
  end

  # connected? turns true inside the connect's critical section, before its subscribe batches are out, so
  # wait for +sent+ messages on the wire too before asserting on them.
  def subscribe_and_connect(resource_id, sent: 1, &handler)
    handler ||= noop
    unsubscribe = dispatcher.subscribe(resource_id, events:, &handler)
    expect(wait_until { dispatcher.connected? && emitted.size >= sent }).to be(true)
    unsubscribe
  end

  # Waits for a reconnect (a second client) and for its first +sent+ messages.
  def await_reconnect(sent:)
    expect(wait_until { clients.size == 2 && dispatcher.connected? && clients.last.emitted_events.size >= sent })
      .to be(true)
  end

  def emitted
    clients.last.emitted_events
  end

  def state_event(resource_id)
    { 'sandbox' => { 'id' => resource_id, 'state' => 'started' }, 'oldState' => 'pending', 'newState' => 'started' }
  end

  it 'does not connect before the first subscription' do
    dispatcher

    expect(Daytona::SocketIOClient).not_to have_received(:new)
    expect(dispatcher.connected?).to be(false)
  end

  it 'connects in the background on the first subscription' do
    subscribe_and_connect('sandbox-1')

    expect(Daytona::SocketIOClient).to have_received(:new).once
  end

  context 'when the connection is not up yet' do
    let(:gate) { Queue.new }

    it 'sends nothing while subscribing, then the whole set in messages of at most 100 ids' do
      ids = Array.new(250) { |i| format('sandbox-%03d', i) }
      unsubscribes = ids.map { |id| dispatcher.subscribe(id, events:, &noop) }
      unsubscribes.last.call
      expect(wait_until { clients.size == 1 }).to be(true)
      expect(emitted).to be_empty

      gate << true
      expect(wait_until { dispatcher.connected? && emitted.size >= 3 }).to be(true)

      expect(emitted.map(&:first)).to eq(%w[subscribe subscribe subscribe])
      expect(emitted.map { |_name, data| data[:sandboxIds].size }).to eq([100, 100, 49])
      expect(emitted.flat_map { |_name, data| data[:sandboxIds] }).to eq(ids[0...249])
    end
  end

  it 'sends subscribe for the first listener of an id and unsubscribe for its last one' do
    first = subscribe_and_connect('sandbox-1')
    expect(emitted).to eq([['subscribe', { sandboxIds: ['sandbox-1'] }]])

    second = dispatcher.subscribe('sandbox-1', events:, &noop)
    first.call
    expect(emitted.size).to eq(1)

    second.call
    expect(emitted.last).to eq(['unsubscribe', { sandboxIds: ['sandbox-1'] }])
  end

  it 'only updates the set while disconnected and sends it again after reconnecting' do
    old_listener = subscribe_and_connect('sandbox-1')
    first_client = clients.first
    changed = false
    # Change the set while the connection is down, before the reconnect attempt.
    allow(dispatcher).to receive(:sleep).with(1) do
      next if changed

      changed = true
      old_listener.call
      dispatcher.subscribe('sandbox-2', events:, &noop)
    end

    first_client.options[:on_disconnect].call

    await_reconnect(sent: 1)
    expect(first_client.emitted_events).to eq([['subscribe', { sandboxIds: ['sandbox-1'] }]])
    expect(clients.last.emitted_events).to eq([['subscribe', { sandboxIds: ['sandbox-2'] }]])
  end

  it 'counts every successful connect, reconnects included, in the connection epoch' do
    expect(dispatcher.connection_epoch).to eq(0)
    subscribe_and_connect('sandbox-1')
    expect(dispatcher.connection_epoch).to eq(1)

    clients.first.options[:on_disconnect].call

    expect(wait_until { clients.size == 2 && dispatcher.connected? }).to be(true)
    expect(dispatcher.connection_epoch).to eq(2)
    expect(Daytona::EventSubscriptionManager.new(dispatcher).connection_epoch).to eq(2)
  end

  context 'with the per-connection subscription cap lowered to 2' do
    before { stub_const('Daytona::EventDispatcher::MAX_SUBSCRIPTIONS_PER_CONNECTION', 2) }

    it 'serves at most the cap, gives a freed slot to the next new id and stays silent for unserved ids' do
      unsubscribe_a = subscribe_and_connect('sandbox-a')
      dispatcher.subscribe('sandbox-b', events:, &noop)
      unsubscribe_c = dispatcher.subscribe('sandbox-c', events:, &noop)
      expect(emitted).to eq([['subscribe', { sandboxIds: ['sandbox-a'] }],
                             ['subscribe', { sandboxIds: ['sandbox-b'] }]])
      expect(dispatcher.served?('sandbox-c')).to be(false)

      unsubscribe_a.call
      dispatcher.subscribe('sandbox-d', events:, &noop)
      expect(emitted.drop(2)).to eq([['unsubscribe', { sandboxIds: ['sandbox-a'] }],
                                     ['subscribe', { sandboxIds: ['sandbox-d'] }]])
      expect(dispatcher.served?('sandbox-c')).to be(false)
      expect(dispatcher.served?('sandbox-d')).to be(true)

      unsubscribe_c.call
      expect(emitted.size).to eq(4)
    end

    it 'sends only the served ids again after reconnecting' do
      subscribe_and_connect('sandbox-a')
      dispatcher.subscribe('sandbox-b', events:, &noop)
      dispatcher.subscribe('sandbox-c', events:, &noop)

      clients.first.options[:on_disconnect].call

      await_reconnect(sent: 1)
      expect(clients.last.emitted_events).to eq([['subscribe', { sandboxIds: %w[sandbox-a sandbox-b] }]])
    end
  end

  it 'is not event-backed for an id that stays served while the connection is down' do
    subscribe_and_connect('sandbox-1')
    expect(dispatcher.event_backed?('sandbox-1')).to be(true)
    expect(dispatcher.event_backed?('sandbox-2')).to be(false)
    # Keep the connection down: the reconnect attempt waits until the example ends.
    allow(dispatcher).to receive(:sleep).with(1) { sleep }

    clients.first.options[:on_disconnect].call

    expect(dispatcher.served?('sandbox-1')).to be(true)
    expect(dispatcher.event_backed?('sandbox-1')).to be(false)
    expect(Daytona::EventSubscriptionManager.new(dispatcher).event_backed?('sandbox-1')).to be(false)
  end

  it 'subscribes to at most 10,000 ids per connection' do
    expect(described_class::MAX_SUBSCRIPTIONS_PER_CONNECTION).to eq(10_000)
  end

  describe 'subscribe acknowledgements' do
    let(:overloaded) { { 'ok' => false, 'error' => 'OVERLOADED', 'message' => 'shedding' } }
    let(:notices) { [] }

    def recording(resource_id)
      proc { |event_name, _data| notices << [resource_id, event_name] }
    end

    context 'with the per-connection subscription cap lowered to 2' do
      before { stub_const('Daytona::EventDispatcher::MAX_SUBSCRIPTIONS_PER_CONNECTION', 2) }

      it 'unserves a rejected id, tells its handlers, frees its slot and leaves it out of the reconnect' do
        subscribe_and_connect('sandbox-a', &recording('sandbox-a'))
        dispatcher.subscribe('sandbox-b', events:, &recording('sandbox-b'))
        expect(wait_until { emitted.size == 2 }).to be(true)

        clients.last.acknowledge(1, overloaded)
        clients.last.acknowledge(0, { 'ok' => true })

        expect(notices).to eq([['sandbox-b', Daytona::EventDispatcher::SUBSCRIPTION_REJECTED_EVENT]])
        expect(dispatcher.served?('sandbox-b')).to be(false)
        expect(dispatcher.event_backed?('sandbox-b')).to be(false)
        expect(dispatcher.event_backed?('sandbox-a')).to be(true)

        dispatcher.subscribe('sandbox-c', events:, &noop)
        expect(emitted.last).to eq(['subscribe', { sandboxIds: ['sandbox-c'] }])

        clients.first.options[:on_disconnect].call
        await_reconnect(sent: 1)
        expect(clients.last.emitted_events).to eq([['subscribe', { sandboxIds: %w[sandbox-a sandbox-c] }]])
        expect(dispatcher.served?('sandbox-b')).to be(false)
      end
    end

    it 'keeps the id served on an ok acknowledgement or without one' do
      subscribe_and_connect('sandbox-a', &recording('sandbox-a'))
      dispatcher.subscribe('sandbox-b', events:, &recording('sandbox-b'))
      expect(wait_until { emitted.size == 2 }).to be(true)

      clients.last.acknowledge(0, { 'ok' => true })

      expect(notices).to be_empty
      expect(dispatcher.event_backed?('sandbox-a')).to be(true)
      expect(dispatcher.event_backed?('sandbox-b')).to be(true)
    end

    it 'ignores a rejection from an earlier connection' do
      subscribe_and_connect('sandbox-a', &recording('sandbox-a'))
      first_client = clients.first
      first_client.options[:on_disconnect].call
      await_reconnect(sent: 1)

      first_client.acknowledge(0, overloaded)

      expect(notices).to be_empty
      expect(dispatcher.served?('sandbox-a')).to be(true)
    end
  end

  it 'removes only the released subscription when the same handler is subscribed twice' do
    received = []
    handler = proc { |_event_name, data| received << data }
    first = subscribe_and_connect('sandbox-1', &handler)
    dispatcher.subscribe('sandbox-1', events:, &handler)

    first.call
    first.call
    clients.last.options[:on_event].call('sandbox.state.updated', state_event('sandbox-1'))

    expect(received.size).to eq(1)
    expect(emitted).to eq([['subscribe', { sandboxIds: ['sandbox-1'] }]])
  end

  it 'still filters incoming events by id' do
    received = []
    subscribe_and_connect('sandbox-1') { |_event_name, data| received << data }

    clients.last.options[:on_event].call('sandbox.state.updated', state_event('sandbox-other'))
    clients.last.options[:on_event].call('sandbox.state.updated', state_event('sandbox-1'))

    expect(received).to eq([state_event('sandbox-1')])
  end

  context 'when emitting fails' do
    let(:failing_emit) { true }

    it 'does not raise into the caller' do
      # Every emit raises, so nothing reaches the wire.
      unsubscribe = subscribe_and_connect('sandbox-1', sent: 0)

      expect { dispatcher.subscribe('sandbox-2', events:, &noop) }.not_to raise_error
      expect { unsubscribe.call }.not_to raise_error
    end
  end
end
