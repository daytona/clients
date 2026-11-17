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
end
