# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::SocketIOClient do
  let(:client) do
    described_class.new(api_url: 'https://api.example.com/api', token: 'token', organization_id: 'org-1',
                        source: 'sdk-ruby', sdk_version: '1.2.3')
  end
  let(:socket) do
    Class.new do
      attr_reader :sent

      def initialize
        @sent = []
      end

      def send(msg)
        @sent << msg
      end
    end.new
  end

  describe 'handshake' do
    it 'opts in to explicit subscriptions next to the existing query parameters' do
      url = URI.parse(client.send(:build_ws_url))
      query = URI.decode_www_form(url.query).to_h

      expect(url.path).to eq('/api/socket.io/')
      expect(query).to include(
        'EIO' => '4', 'transport' => 'websocket', 'organizationId' => 'org-1', 'source' => 'sdk-ruby',
        'sdkVersion' => '1.2.3', 'subscriptionMode' => 'explicit'
      )
    end
  end

  describe '#emit' do
    before { client.instance_variable_set(:@ws, socket) }

    it 'writes a Socket.IO event packet without an acknowledgement id' do
      client.instance_variable_set(:@connected, true)

      expect(client.emit('subscribe', { sandboxIds: %w[a b] })).to be(true)
      expect(socket.sent).to eq(['42["subscribe",{"sandboxIds":["a","b"]}]'])
    end

    it 'drops the event instead of buffering it while not connected' do
      expect(client.emit('subscribe', { sandboxIds: ['a'] })).to be(false)

      client.instance_variable_set(:@connected, true)
      client.emit('unsubscribe', { sandboxIds: ['b'] })
      expect(socket.sent).to eq(['42["unsubscribe",{"sandboxIds":["b"]}]'])
    end

    it 'does not raise when the write fails' do
      client.instance_variable_set(:@connected, true)
      allow(socket).to receive(:send).and_raise(IOError, 'closed')

      expect(client.emit('subscribe', { sandboxIds: ['a'] })).to be(false)
    end
  end

  describe '#emit_with_ack' do
    let(:acks) { [] }

    before do
      client.instance_variable_set(:@ws, socket)
      client.instance_variable_set(:@connected, true)
    end

    def emit_recording(name, ids)
      client.emit_with_ack('subscribe', { sandboxIds: ids }) { |ack| acks << [name, ack] }
    end

    it 'gives each acknowledged event a packet id and runs its callback once, on the matching ACK' do
      emit_recording('first', ['a'])
      emit_recording('second', ['b'])
      client.emit('unsubscribe', { sandboxIds: ['c'] })
      expect(socket.sent).to eq(['421["subscribe",{"sandboxIds":["a"]}]', '422["subscribe",{"sandboxIds":["b"]}]',
                                 '42["unsubscribe",{"sandboxIds":["c"]}]'])

      client.send(:handle_socketio_packet, '32[{"ok":false,"error":"OVERLOADED"}]', nil)
      client.send(:handle_socketio_packet, '399[{"ok":true}]', nil)
      client.send(:handle_socketio_packet, '31[{"ok":true}]', nil)
      client.send(:handle_socketio_packet, '31[{"ok":true}]', nil)

      expect(acks).to eq([['second', { 'ok' => false, 'error' => 'OVERLOADED' }], ['first', { 'ok' => true }]])
    end

    it 'drops the pending callbacks when the connection goes' do
      emit_recording('first', ['a'])

      client.send(:handle_socketio_packet, '1', nil)
      client.instance_variable_set(:@connected, true)
      client.send(:handle_socketio_packet, '31[{"ok":false,"error":"OVERLOADED"}]', nil)

      expect(acks).to be_empty
    end

    it 'drops a callback whose acknowledgement did not come in time' do
      now = 100.0
      allow(Process).to receive(:clock_gettime).and_call_original
      allow(Process).to receive(:clock_gettime).with(Process::CLOCK_MONOTONIC) { now }
      emit_recording('first', ['a'])

      now += described_class::ACK_TIMEOUT
      client.send(:handle_socketio_packet, '31[{"ok":true}]', nil)

      expect(acks).to be_empty
    end

    it 'sends nothing and registers no callback while not connected' do
      client.instance_variable_set(:@connected, false)

      expect(emit_recording('first', ['a'])).to be(false)
      expect(socket.sent).to be_empty
    end
  end
end
