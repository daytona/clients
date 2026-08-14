# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::ProcessHandle do
  let(:api_client) do
    double(
      'ApiClient',
      default_headers: { 'Authorization' => 'Bearer token' },
      config: double('Config', scheme: 'https', host: 'proxy.example.com', base_path: '/toolbox/sandbox-123')
    )
  end
  let(:toolbox_api) { instance_double(DaytonaToolboxApiClient::ProcessApi, api_client:) }
  let(:handle) { described_class.new(process_id: 'proc-1', toolbox_api:) }

  describe Daytona::ProcessRunFrameDecoder do
    it 'holds split UTF-8 bytes independently by output channel and flushes dangling bytes with replacement' do
      decoder = described_class.new

      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64("\xF0\x9F"), encoding: 'base64'))
        .to eq([:stdout, ''])
      expect(decoder.decode(channel: 'stderr', data: Base64.strict_encode64("err\n"), encoding: 'base64'))
        .to eq([:stderr, "err\n"])
      expect(decoder.decode(channel: 'pty', data: Base64.strict_encode64("\x98\x80 ok\n"), encoding: 'base64'))
        .to eq([:stdout, "😀 ok\n"])
      expect(decoder.decode(channel: 'stderr', data: Base64.strict_encode64("\xE2"), encoding: 'base64'))
        .to eq([:stderr, ''])

      expect(decoder.flush).to eq(['', '�'])
    end

    it 'replaces malformed bytes immediately instead of withholding later frames' do
      decoder = described_class.new
      frames = [+"first\xFF", +"second\n", +"\xF0\x9F", +"\x98\x80 ok\n", +"bad\xC3", +"\x28 end\n"]

      decoded = frames.map do |raw|
        decoder.decode(channel: 'stdout', data: Base64.strict_encode64(raw), encoding: 'base64')
      end

      expect(decoded).to eq(
        [
          [:stdout, "first\u{FFFD}"], [:stdout, "second\n"], [:stdout, ''],
          [:stdout, "\u{1F600} ok\n"], [:stdout, 'bad'], [:stdout, "\u{FFFD}( end\n"]
        ]
      )
      expect(decoder.flush).to eq(['', ''])
    end

    it 'holds a trailing byte that can still complete a codepoint until flush' do
      decoder = described_class.new

      expect(decoder.decode(channel: 'stderr', data: Base64.strict_encode64(+"ok\xF0"), encoding: 'base64'))
        .to eq([:stderr, 'ok'])
      expect(decoder.flush).to eq(['', "\u{FFFD}"])
    end

    it 'replaces impossible UTF-8 prefixes immediately instead of buffering them' do
      # Python codecs incremental parity: E0 80 / F0 80 80 / F4 90 can never
      # complete and are replaced at once; ED A0 is retained until more input.
      decoder = described_class.new
      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"a\xE0\x80"), encoding: 'base64'))
        .to eq([:stdout, "a\u{FFFD}\u{FFFD}"])
      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"b\n"), encoding: 'base64'))
        .to eq([:stdout, "b\n"])

      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"x\xF0\x80\x80"), encoding: 'base64'))
        .to eq([:stdout, "x\u{FFFD}\u{FFFD}\u{FFFD}"])
      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"y\xF4\x90"), encoding: 'base64'))
        .to eq([:stdout, "y\u{FFFD}\u{FFFD}"])

      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"q\xED\xA0"), encoding: 'base64'))
        .to eq([:stdout, 'q'])
      expect(decoder.decode(channel: 'stdout', data: Base64.strict_encode64(+"\n"), encoding: 'base64'))
        .to eq([:stdout, "\u{FFFD}\u{FFFD}\n"])
    end
  end

  describe '#stream_logs' do
    it 'raises ConnectionError when the log stream times out' do
      allow(api_client).to receive(:build_request_url).with('/processes/proc-1/logs')
                                                      .and_return('https://proxy.example.com/toolbox/sandbox-123/processes/proc-1/logs')
      allow(Net::HTTP).to receive(:start).and_raise(Net::ReadTimeout)

      expect { handle.stream_logs { |_event| nil } }
        .to raise_error(Daytona::Sdk::ConnectionError, /Failed to stream process logs/)
    end
  end

  describe '#attach_terminal' do
    before do
      allow(toolbox_api).to receive(:get_process).with('proc-1').and_return(double('Process', kind: 'pty'))
      allow(api_client).to receive(:build_request_url).with('/processes/proc-1/attach')
                                                      .and_return('https://proxy.example.com/toolbox/sandbox-123/processes/proc-1/attach')
    end

    [Errno::ECONNREFUSED, Timeout::Error, IOError].each do |error_class|
      it "raises ConnectionError when the terminal socket fails with #{error_class}" do
        allow(WebSocket::Client::Simple).to receive(:connect).and_raise(error_class)

        expect { handle.attach_terminal }
          .to raise_error(Daytona::Sdk::ConnectionError, /Failed to attach process terminal/)
      end
    end

    it 'still raises a plain Sdk::Error for non-PTY processes' do
      allow(toolbox_api).to receive(:get_process).with('proc-1').and_return(double('Process', kind: 'exec'))

      expect { handle.attach_terminal }
        .to raise_error(Daytona::Sdk::Error, /only supported for kind=pty/)
    end
  end

  describe '#output' do
    it 'paginates retained base64 logs and returns process terminal metadata' do
      process_record = double('Process', exit_code: 7, signal: nil, reason: 'exited')
      first = double(
        'Page', eof: false, next_cursor: 'next',
                frames: [double(channel: 'stdout', data: Base64.strict_encode64("\xF0\x9F"), encoding: 'base64')]
      )
      second = double(
        'Page', eof: true, next_cursor: 'done',
                frames: [double(channel: 'stdout', data: Base64.strict_encode64("\x98\x80\n"), encoding: 'base64')]
      )
      allow(toolbox_api).to receive(:get_process).with('proc-1').and_return(process_record)
      allow(toolbox_api).to receive(:read_process_logs)
        .with('proc-1', cursor: 'start', limit: 1000, encoding: 'base64').and_return(first)
      allow(toolbox_api).to receive(:read_process_logs)
        .with('proc-1', cursor: 'next', limit: 1000, encoding: 'base64').and_return(second)

      result = handle.output

      expect(result.stdout).to eq("😀\n")
      expect(result.stderr).to eq('')
      expect(result.exit_code).to eq(7)
      expect(result.reason).to eq('exited')
    end
  end
end
