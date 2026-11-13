# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::SandboxNodes do
  let(:api) do
    cfg = DaytonaApiClient::Configuration.new
    cfg.scheme = 'https'
    cfg.host = 'api.example.com'
    DaytonaApiClient::SandboxApi.new(DaytonaApiClient::ApiClient.new(cfg))
  end
  let(:manager) { instance_double(Daytona::EventSubscriptionManager, subscribe: 'sub') }
  let(:sandbox) do
    Daytona::Sandbox.new(sandbox_dto: gang, config: build_config, sandbox_api: api, subscription_manager: manager)
  end
  let(:base) { 'https://proxy.example.com' }

  def gang(count = 2, generation = 4, proxy = 'https://proxy.example.com')
    nodes = count.times.map do |rank|
      DaytonaApiClient::SandboxNode.new(id: rank.zero? ? 'sandbox-123' : "worker-#{rank}", rank:,
                                        cpu: 8, gpu: 8, memory: 64, disk: 100, state: 'started', incarnation: 1)
    end
    build_sandbox_dto(gpu: count * 8, node_count: count, topology_generation: generation, nodes:,
                      toolbox_proxy_url: proxy)
  end

  def sessions(completed: true, cleanup_status: 200)
    stub_request(:post, %r{#{base}/[^/]+/process/execute}).to_return(
      body: '{"exitCode":0,"result":""}', headers: { 'Content-Type' => 'application/json' }
    )
    stub_request(:post, %r{#{base}/[^/]+/process/session$}).to_return(
      body: '{"sessionId":"private"}', headers: { 'Content-Type' => 'application/json' }
    )
    stub_request(:post, %r{#{base}/[^/]+/process/session/[^/]+/exec$}).to_return(
      body: '{"cmdId":"cmd"}', headers: { 'Content-Type' => 'application/json' }
    )
    command = { id: 'cmd', command: 'echo ok' }
    command[:exitCode] = 0 if completed
    stub_request(:get, %r{#{base}/[^/]+/process/session/[^/]+/command/cmd$}).to_return(
      body: command.to_json, headers: { 'Content-Type' => 'application/json' }
    )
    stub_request(:get, %r{#{base}/[^/]+/process/session/[^/]+/command/cmd/logs$}).to_return(
      body: '{"output":"ok","stdout":"ok","stderr":""}', headers: { 'Content-Type' => 'application/json' }
    )
    stub_request(:delete, %r{#{base}/[^/]+/process/session/[^/]+$}).to_return(status: cleanup_status, body: '{}')
  end

  it 'retains node and process identity and reroutes partial refreshes' do
    worker = sandbox.nodes[1]
    process = worker.process
    dto = gang(2, 4, 'https://new.example.com/toolbox')
    dto.nodes = nil
    sandbox.send(:process_response, dto)
    expect(sandbox.nodes[1]).to equal(worker)
    expect(worker.process).to equal(process)
    stub_request(:post, 'https://new.example.com/toolbox/worker-1/process/execute').with(
      headers: { 'Authorization' => "Bearer #{build_config.api_key}" }
    ).to_return(body: '{"exitCode":0,"result":"worker"}', headers: { 'Content-Type' => 'application/json' })
    expect(process.exec(command: 'echo worker').result).to eq('worker')
  end

  it 'rejects malformed membership before replacing existing handles' do
    worker = sandbox.nodes[1]
    malformed = gang
    malformed.nodes[1].id = '..'
    expect { sandbox.send(:process_response, malformed) }.to raise_error(Daytona::Sdk::Error)
    expect(sandbox.nodes[1]).to equal(worker)
  end

  it 'refreshes the generation before adding and refreshes membership afterwards' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    worker = sandbox.nodes[1]
    stub_request(:get, url).to_return(
      { body: gang(2, 5).to_hash.to_json, headers: { 'Content-Type' => 'application/json' } },
      { body: gang(3, 6).to_hash.to_json, headers: { 'Content-Type' => 'application/json' } }
    )
    stub_request(:post, "#{url}/nodes").with(body: hash_including('expectedGeneration' => 5)).to_return(
      body: '{"id":"op","sandboxId":"sandbox-123","type":"ADD","status":"COMPLETED","phase":"COMPLETED","expectedGeneration":5,"targetGeneration":6,"policyRevision":1,"createdAt":"2026-01-01T00:00:00Z","updatedAt":"2026-01-01T00:00:00Z"}',
      headers: { 'Content-Type' => 'application/json' }
    )
    sandbox.nodes.add(nodes: [Daytona::NodeResources.new(cpu: 8, memory: 64, disk: 100)])
    expect(sandbox.nodes.size).to eq(3)
    expect(sandbox.topology_generation).to eq(6)
    expect(sandbox.nodes[1]).to equal(worker)
  end

  it 'cleans every session after a non-streaming timeout and reports all failed ranks' do
    sessions(completed: false)
    expect { sandbox.nodes.process.exec(command: 'sleep 100', timeout: 0.03) }.to raise_error(
      Daytona::Sdk::NodeOperationError
    ) do |error|
      expect(error.failures.keys).to eq([0, 1])
      expect(error.failures.values).to all(be_a(Daytona::Sdk::TimeoutError))
    end
    expect(a_request(:delete, %r{/process/session/})).to have_been_made.twice
  end

  it 'closes idle streams and cleans sessions before returning timeout errors' do
    sessions(completed: false)
    sockets = Queue.new
    allow(Daytona::Common::WebSocketDialer).to receive(:connect) do |_, **_, &block|
      socket = double('socket')
      allow(socket).to receive(:on)
      allow(socket).to receive(:close)
      sockets << socket
      block.call(socket)
      socket
    end
    callback = ->(_, _) { raise 'No idle stream output expected' }
    expect { sandbox.nodes.process.exec(command: 'sleep 100', timeout: 0.03, on_stdout: callback) }
      .to raise_error(Daytona::Sdk::NodeOperationError) do |error|
        expect(error.failures.values.map { |e| [e.class, e.message] }).to all(include(Daytona::Sdk::TimeoutError))
      end
    expect(sockets.size).to eq(2)
    2.times { expect(sockets.pop(true)).to have_received(:close) }
    expect(a_request(:delete, %r{/process/session/})).to have_been_made.twice
  end

  it 'keeps successful results when cleanup fails and records the warning on each node' do
    sessions(cleanup_status: 500)
    expect(sandbox.nodes.process.exec(command: 'echo ok').transform_values(&:result)).to eq(0 => 'ok', 1 => 'ok')
    sandbox.nodes.each { |node| expect(node.warnings.join).to include('cleanup failed') }
  end

  it 'treats RDMA preflight failure as advisory' do
    sessions
    stub_request(:post, %r{#{base}/[^/]+/process/execute}).to_return(status: 500)
    results = sandbox.nodes.process.exec(command: 'echo ok')
    expect(results.keys).to eq([0, 1])
    expect(results.values.map(&:result)).to all(include('WARNING:', 'ok'))
  end

  it 'tags streamed chunks by rank and closes streams after successful commands' do
    sessions
    chunks = []
    closed = Queue.new
    allow(Daytona::Common::WebSocketDialer).to receive(:connect) do |_, **_, &block|
      handlers = {}
      socket = double('socket')
      allow(socket).to receive(:on) { |event, &handler| handlers[event] = handler }
      allow(socket).to receive(:close) { closed.push(true) }
      block.call(socket)
      handlers[:message].call(double(type: :text, data: "\x01\x01\x01ok"))
      handlers[:close].call
      socket
    end
    results = sandbox.nodes.process.exec(command: 'echo ok', on_stdout: ->(rank, chunk) { chunks << [rank, chunk] })
    expect(results.keys).to eq([0, 1])
    expect(chunks).to contain_exactly([0, 'ok'], [1, 'ok'])
    expect(closed.size).to eq(2)
    expect(a_request(:delete, %r{/process/session/})).to have_been_made.twice
  end

  it 'reports every failed upload without rolling back successful uploads' do
    sandbox.nodes.each { |node| allow(node.fs).to receive(:upload_file).and_raise(Daytona::Sdk::Error, 'upload failed') }
    expect do
      sandbox.nodes.fs.upload_file('content', '/tmp/file')
    end.to raise_error(Daytona::Sdk::NodeOperationError) do |error|
      expect(error.failures.keys).to eq([0, 1])
    end
  end

  it 'routes preview and SSH requests to the selected member id' do
    expect(api).to receive(:get_port_preview_url).with('worker-1', 8080)
    expect(api).to receive(:create_ssh_access).with('worker-1', { expires_in_minutes: 60 })
    expect(api).to receive(:revoke_ssh_access).with('worker-1', { token: 'token' })
    sandbox.nodes[1].preview_url(8080)
    sandbox.nodes[1].create_ssh_access(60)
    sandbox.nodes[1].revoke_ssh_access('token')
  end

  it 'preserves scalar resource inputs and accepts positive per-node arrays' do
    expect(Daytona::Resources.new(cpu: 4).to_h).to eq(cpu: 4)
    expect(Daytona::Resources.new(cpu: [4, 8], memory: [16, 32]).to_h).to eq(cpu: [4, 8], memory: [16, 32])
    expect { Daytona::Resources.new(cpu: []) }.to raise_error(Daytona::Sdk::Error)
    expect { Daytona::Resources.new(memory: [1, -2]) }.to raise_error(Daytona::Sdk::Error)
  end

  it 'includes generation refresh in the membership deadline and does not submit after expiry' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    stub_request(:get, url).to_return do
      sleep 0.02
      { body: gang.to_hash.to_json, headers: { 'Content-Type' => 'application/json' } }
    end
    expect { sandbox.nodes.release(timeout: 0.001) }.to raise_error(Daytona::Sdk::TimeoutError)
    expect(a_request(:post, "#{url}/nodes/remove")).not_to have_been_made
  end

  it 'surfaces failed membership operations without a final refresh' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    stub_request(:get, url).to_return(body: gang.to_hash.to_json, headers: { 'Content-Type' => 'application/json' })
    operation = {
      id: 'op', sandboxId: sandbox.id, type: 'REMOVE', status: 'FAILED', phase: 'FENCE',
      expectedGeneration: 4, targetGeneration: 5, policyRevision: 1,
      errorReason: 'replacement capacity unavailable', createdAt: '2026-01-01', updatedAt: '2026-01-01'
    }
    stub_request(:post, "#{url}/nodes/remove").with(body: hash_including('expectedGeneration' => 4, 'count' => 1))
                                              .to_return(body: operation.to_json, headers: { 'Content-Type' => 'application/json' })
    expect { sandbox.nodes.release }.to raise_error(Daytona::Sdk::Error, 'replacement capacity unavailable')
    expect(a_request(:get, url)).to have_been_made.once
  end
end
