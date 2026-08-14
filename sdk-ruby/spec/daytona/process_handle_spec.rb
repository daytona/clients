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
