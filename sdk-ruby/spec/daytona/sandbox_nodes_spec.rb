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
  let(:manager) { instance_double(Daytona::EventSubscriptionManager, subscribe: 'sub', refresh: true) }
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

  it 'refreshes the existing rank-zero handle when an ordinary sandbox omits nodes' do
    dto = build_sandbox_dto(cpu: 2, state: 'started')
    ordinary = Daytona::Sandbox.new(sandbox_dto: dto, config: build_config, sandbox_api: api,
                                    subscription_manager: manager)
    head = ordinary.nodes.first
    allow(api).to receive(:get_sandbox).with(dto.id).and_return(build_sandbox_dto(cpu: 6, memory: 12, disk: 40,
                                                                                  state: 'stopped'))
    ordinary.refresh
    expect(ordinary.nodes.first).to equal(head)
    expect(head).to have_attributes(cpu: 6, memory: 12, disk: 40, state: 'stopped')
  end

  it 'waits for member details before exposing ranks on a fresh gang' do
    sparse = gang
    sparse.nodes = nil
    head = Daytona::Sandbox.new(sandbox_dto: sparse, config: build_config, sandbox_api: api,
                                subscription_manager: manager)
    nodes = head.nodes

    expect(head.node_count).to eq(2)
    expect(nodes.to_a).to be_empty
    expect { nodes.process.exec(command: 'hostname') }.to raise_error(Daytona::Sdk::Error, /membership is unavailable/)
    expect { nodes.fs.upload_file('data', '/file') }.to raise_error(Daytona::Sdk::Error, /membership is unavailable/)

    allow(api).to receive(:get_sandbox).and_return(gang)
    head.refresh
    expect(nodes.map(&:id)).to eq([head.id, 'worker-1'])
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

  it 'submits the cached generation and refreshes membership after completion' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    worker = sandbox.nodes[1]
    stub_request(:get, url).to_return(
      { body: gang(3, 5).to_hash.to_json, headers: { 'Content-Type' => 'application/json' } }
    )
    stub_request(:post, "#{url}/nodes").with(body: hash_including('expectedGeneration' => 4)).to_return(
      body: '{"id":"op","sandboxId":"sandbox-123","type":"ADD","status":"COMPLETED","phase":"COMPLETED","expectedGeneration":4,"targetGeneration":5,"policyRevision":1,"createdAt":"2026-01-01T00:00:00Z","updatedAt":"2026-01-01T00:00:00Z"}',
      headers: { 'Content-Type' => 'application/json' }
    )
    sandbox.nodes.add(nodes: [Daytona::NodeResources.new(cpu: 8, memory: 64, disk: 100)])
    expect(sandbox.nodes.size).to eq(3)
    expect(sandbox.topology_generation).to eq(5)
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

  it 'reuses the probe until a replacement and preserves the successful rank on partial failure' do
    sessions
    sandbox.nodes.process.exec(command: 'first')
    sandbox.nodes.process.exec(command: 'second')
    updated = gang
    updated.nodes[1].incarnation = 2
    sandbox.nodes.send(:update, updated)
    stub_request(:post, %r{#{base}/worker-1/process/session/[^/]+/exec$}).to_return(status: 503)
    expect { sandbox.nodes.process.exec(command: 'third') }.to raise_error(Daytona::Sdk::NodeOperationError) do |error|
      expect(error.results.keys).to eq([0])
      expect(error.failures.keys).to eq([1])
    end
    expect(a_request(:post, "#{base}/worker-1/process/execute")).to have_been_made.twice
    expect(a_request(:post, "#{base}/sandbox-123/process/execute")).to have_been_made.once
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

  it 'uploads the exact bytes of an IO on every rank' do
    bytes = [1, 0, 2].pack('C*')
    sandbox.nodes.each do |node|
      expect(node.fs.toolbox_api).to receive(:upload_file) do |dest, file:|
        expect(dest).to eq('/sample')
        expect(file.read).to eq(bytes)
      end
    end
    sandbox.nodes.fs.upload_file(StringIO.new(bytes), '/sample')
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
    expect(Daytona::Resources.new(cpu_per_node: [4, 8],
                                  memory_per_node: [16,
                                                    32]).to_h).to eq(cpu_per_node: [4, 8], memory_per_node: [16, 32])
    expect { Daytona::Resources.new(cpu_per_node: []) }.to raise_error(Daytona::Sdk::Error)
    expect { Daytona::Resources.new(memory_per_node: [1, -2]) }.to raise_error(Daytona::Sdk::Error)
  end

  it 'surfaces a conflict for a stale release without refreshing or retrying' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    stub_request(:post, "#{url}/nodes/remove").with(body: hash_including('expectedGeneration' => 4))
                                              .to_return(status: 409, body: '{"message":"Topology changed"}')
    expect { sandbox.nodes.release }.to raise_error(Daytona::Sdk::ConflictError)
    expect(a_request(:get, url)).not_to have_been_made
    expect(a_request(:post, "#{url}/nodes/remove")).to have_been_made.once
  end

  it 'retains the accepted operation ID on a membership timeout' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    operation = { id: 'op', sandboxId: sandbox.id, type: 'REMOVE', status: 'RUNNING', phase: 'FENCE',
                  expectedGeneration: 4, targetGeneration: 5, policyRevision: 1, createdAt: '2026-01-01', updatedAt: '2026-01-01' }
    stub_request(:post, "#{url}/nodes/remove").to_return(body: operation.to_json,
                                                         headers: { 'Content-Type' => 'application/json' })
    expect { sandbox.nodes.release(timeout: 0.25) }.to raise_error(Daytona::Sdk::TimeoutError) do |error|
      expect(error.operation_id).to eq('op')
    end
  end

  it 'returns a completed release even when updating the cached nodes fails' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    headers = { 'Content-Type' => 'application/json' }
    stub_request(:get, url).to_return(status: 503)
    operation = { id: 'op', sandboxId: sandbox.id, type: 'REMOVE', status: 'COMPLETED', phase: 'COMPLETED',
                  expectedGeneration: 4, targetGeneration: 5, policyRevision: 1,
                  createdAt: '2026-01-01', updatedAt: '2026-01-01' }
    stub_request(:post, "#{url}/nodes/remove").to_return(body: operation.to_json, headers:)
    expect(sandbox.nodes.release.id).to eq('op')
    expect(a_request(:post, "#{url}/nodes/remove")).to have_been_made.once
  end

  it 'opens a worker by its own id' do
    dto = gang
    dto.head_sandbox_id = dto.id
    dto.id = dto.nodes.last.id
    dto.node_rank = 1
    dto.nodes = [dto.nodes.last]
    worker = Daytona::Sandbox.new(sandbox_dto: dto, config: build_config, sandbox_api: api,
                                  subscription_manager: manager)
    expect(worker.nodes.first.rank).to eq(1)
    expect(worker.nodes.first.id).to eq(dto.id)

    dto.nodes = nil
    dto.cpu = 12
    sparse = Daytona::Sandbox.new(sandbox_dto: dto, config: build_config, sandbox_api: api,
                                  subscription_manager: manager)
    expect(sparse.nodes.first).to have_attributes(rank: 1, id: dto.id, cpu: 12)
  end

  it 'identifies the accepted operation when polling fails' do
    url = "#{api.api_client.config.base_url}/sandbox/sandbox-123"
    headers = { 'Content-Type' => 'application/json' }
    stub_request(:get, url).to_return(body: gang.to_hash.to_json, headers:)
    operation = { id: 'op', sandboxId: sandbox.id, type: 'REMOVE', status: 'RUNNING', phase: 'FENCE',
                  expectedGeneration: 4, targetGeneration: 5, policyRevision: 1, createdAt: '2026-01-01', updatedAt: '2026-01-01' }
    stub_request(:post, "#{url}/nodes/remove").to_return(body: operation.to_json, headers:)
    stub_request(:get, "#{url}/membership-operations/op").to_return(status: 503)
    expect { sandbox.nodes.release }.to raise_error(Daytona::Sdk::Error) do |error|
      expect(error.operation_id).to eq('op')
    end
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
    expect { sandbox.nodes.release }.to raise_error(Daytona::Sdk::Error, 'replacement capacity unavailable') do |error|
      expect(error.operation_id).to eq('op')
    end
    expect(a_request(:get, url)).not_to have_been_made
  end
end
