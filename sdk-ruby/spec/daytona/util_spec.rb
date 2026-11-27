# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::Util do
  describe '.demux' do
    let(:stdout_prefix) { "\x01\x01\x01" }
    let(:stderr_prefix) { "\x02\x02\x02" }

    it 'separates stdout and stderr from multiplexed output' do
      stdout, stderr = described_class.demux("#{stdout_prefix}hello#{stderr_prefix}error")

      expect(stdout).to eq('hello')
      expect(stderr).to eq('error')
    end

    it 'handles stdout-only output' do
      stdout, stderr = described_class.demux("#{stdout_prefix}hello world")

      expect(stdout).to eq('hello world')
      expect(stderr).to eq('')
    end

    it 'handles stderr-only output' do
      stdout, stderr = described_class.demux("#{stderr_prefix}error only")

      expect(stdout).to eq('')
      expect(stderr).to eq('error only')
    end

    it 'handles empty input' do
      stdout, stderr = described_class.demux('')

      expect(stdout).to eq('')
      expect(stderr).to eq('')
    end

    it 'handles interleaved stdout and stderr chunks' do
      stdout, stderr = described_class.demux("#{stdout_prefix}line1#{stderr_prefix}err1#{stdout_prefix}line2")

      expect(stdout).to eq('line1line2')
      expect(stderr).to eq('err1')
    end

    it 'ignores text before the first stream prefix' do
      stdout, stderr = described_class.demux("orphan#{stdout_prefix}hello")

      expect(stdout).to eq('hello')
      expect(stderr).to eq('')
    end
  end

  describe Daytona::Util::Demuxer do
    def demux_messages(messages)
      demuxer = described_class.new
      stdout = +''
      stderr = +''
      (messages.map { |message| demuxer.feed(message) } << demuxer.flush).each do |out, err|
        stdout << out
        stderr << err
      end
      [stdout, stderr]
    end

    [
      [["\x01\x01\x01line1\n", "line2\n"], ["line1\nline2\n", '']],
      [["\x01\x01\x01line1\n", "line2\n\x02\x02\x02err\n"], ["line1\nline2\n", "err\n"]],
      [["\x01\x01\x01out\n\x02\x02", "\x02err\n"], ["out\n", "err\n"]],
      [["\x01\x01\x01out\n\x02", "\x02\x02err\n"], ["out\n", "err\n"]],
      [["\x01\x01\x01", "out\n"], ["out\n", '']],
      [["\x01\x01\x01out\n\x02\x02\x02", "err\n"], ["out\n", "err\n"]],
      [["\x01", "\x01", "\x01out\n"], ["out\n", '']]
    ].each do |messages, expected|
      it "keeps the stream across messages #{messages.inspect}" do
        expect(demux_messages(messages)).to eq(expected)
      end
    end

    it 'gives the same output wherever the stream is split' do
      stream = "\x01\x01\x01hello\n\x02\x02\x02oops\n\x01\x01\x01bye\n"
      expected = %W[hello\nbye\n oops\n]

      (1...stream.bytesize).each do |offset|
        messages = [stream.byteslice(0, offset), stream.byteslice(offset..)]
        expect(demux_messages(messages)).to eq(expected), "split at byte #{offset}"
      end
      expect(demux_messages(stream.each_char.to_a)).to eq(expected)
    end

    it 'holds back a trailing partial marker until the next message' do
      demuxer = described_class.new

      expect(demuxer.feed("\x01\x01\x01out\x02\x02")).to eq(['out', ''])
      expect(demuxer.feed("\x02err")).to eq(['', 'err'])
    end

    it 'flushes held-back bytes that never complete a marker as output of the current stream' do
      demuxer = described_class.new

      expect(demuxer.feed("\x01\x01\x01out\x02\x02")).to eq(['out', ''])
      expect(demuxer.flush).to eq(["\x02\x02", ''])
      expect(demuxer.flush).to eq(['', ''])
    end

    it 'emits bytes that only look like the start of a marker once the next message rules it out' do
      expect(demux_messages(["\x02\x02\x02a\x01\x01", 'b'])).to eq(['', "a\x01\x01b"])
    end

    it 'drops output only before the first marker of the whole stream' do
      expect(demux_messages(['orphan', "\x01\x01\x01hello", ' world'])).to eq(['hello world', ''])
    end

    it 'keeps the encoding of the incoming data' do
      stdout, = described_class.new.feed("\x01\x01\x01h\u00e9llo")

      expect(stdout).to eq("h\u00e9llo")
      expect(stdout.encoding).to eq(Encoding::UTF_8)
    end
  end

  describe '.stream_async' do
    it 'streams chunks asynchronously to the callback' do
      http = double('Http')
      response = double('Response')
      captured_request = nil
      received = []

      allow(Net::HTTP).to receive(:start).with('example.com', 443, use_ssl: true).and_yield(http)
      allow(http).to receive(:request) do |request, &block|
        captured_request = request
        block.call(response)
      end
      allow(response).to receive(:read_body).and_yield('first').and_yield('second')

      thread = described_class.stream_async(
        uri: URI('https://example.com/logs'),
        headers: { 'Authorization' => 'Bearer token' },
        on_chunk: ->(chunk) { received << chunk }
      )
      thread.join

      expect(captured_request['Authorization']).to eq('Bearer token')
      expect(received).to eq(%w[first second])
    end

    it 'swallows read timeouts' do
      allow(Net::HTTP).to receive(:start).and_raise(Net::ReadTimeout)

      expect { described_class.stream_async(uri: URI('https://example.com/logs'), on_chunk: ->(_chunk) {}).join }
        .not_to raise_error
    end
  end
end
