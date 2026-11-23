# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'benchmark'

RSpec.describe Daytona::Sandbox do
  let(:config) { build_config }
  let(:sandbox_api) { instance_double(DaytonaApiClient::SandboxApi) }
  let(:sandbox_dto) { build_sandbox_dto }
  let(:toolbox_client) do
    double('ToolboxApiClient', default_headers: {}).tap do |client|
      allow(client).to receive(:user_agent=)
    end
  end
  let(:process_api) { instance_double(DaytonaToolboxApiClient::ProcessApi) }
  let(:fs_api) { instance_double(DaytonaToolboxApiClient::FileSystemApi) }
  let(:git_api) { instance_double(DaytonaToolboxApiClient::GitApi) }
  let(:lsp_api) { instance_double(DaytonaToolboxApiClient::LspApi) }
  let(:computer_use_api) { instance_double(DaytonaToolboxApiClient::ComputerUseApi) }
  let(:interpreter_api) { instance_double(DaytonaToolboxApiClient::InterpreterApi) }
  let(:info_api) { instance_double(DaytonaToolboxApiClient::InfoApi) }
  let(:server_api) { instance_double(DaytonaToolboxApiClient::ServerApi) }
  let(:subscription_manager) do
    instance_double(
      Daytona::EventSubscriptionManager,
      subscribe: 'sub-123',
      refresh: true,
      unsubscribe: nil,
      connected?: true,
      event_backed?: true,
      connection_epoch: 0
    )
  end

  before do
    allow(DaytonaToolboxApiClient::ApiClient).to receive(:new).and_return(toolbox_client)
    allow(DaytonaToolboxApiClient::ProcessApi).to receive(:new).and_return(process_api)
    allow(DaytonaToolboxApiClient::FileSystemApi).to receive(:new).and_return(fs_api)
    allow(DaytonaToolboxApiClient::GitApi).to receive(:new).and_return(git_api)
    allow(DaytonaToolboxApiClient::LspApi).to receive(:new).and_return(lsp_api)
    allow(DaytonaToolboxApiClient::ComputerUseApi).to receive(:new).and_return(computer_use_api)
    allow(DaytonaToolboxApiClient::InterpreterApi).to receive(:new).and_return(interpreter_api)
    allow(DaytonaToolboxApiClient::InfoApi).to receive(:new).and_return(info_api)
    allow(DaytonaToolboxApiClient::ServerApi).to receive(:new).and_return(server_api)
    # State waits poll through a copy of sandbox_api with an HTTP timeout; the copy is sandbox_api itself here.
    allow(sandbox_api).to receive(:api_client).and_return(sandbox_api_client)
    allow(DaytonaApiClient::SandboxApi).to receive(:new).and_return(sandbox_api)
  end

  let(:sandbox_api_client) { DaytonaApiClient::ApiClient.new(DaytonaApiClient::Configuration.new) }

  let(:sandbox) do
    described_class.new(
      sandbox_dto: sandbox_dto,
      config: config,
      sandbox_api: sandbox_api,
      subscription_manager: subscription_manager
    )
  end

  # Stubs the monotonic clock so that it only moves while #wait_for_state waits between polls, so the
  # deadline examples take no real time. Returns a proc that reads the clock.
  def stub_wait_clock
    clock = 0.0
    allow(Process).to receive(:clock_gettime).and_call_original
    allow(Process).to receive(:clock_gettime).with(Process::CLOCK_MONOTONIC) { clock }
    stub_condition_variable_waits { |seconds| clock += seconds }
    -> { clock }
  end

  # Makes ConditionVariables created from here on return from #wait at once, passing its timeout to +on_wait+.
  def stub_condition_variable_waits(&on_wait)
    allow(ConditionVariable).to receive(:new).and_wrap_original do |original|
      original.call.tap { |cv| allow(cv).to receive(:wait) { |_mutex, seconds| on_wait.call(seconds) } }
    end
  end

  describe '#initialize' do
    it 'populates attributes from DTO' do
      expect(sandbox.id).to eq('sandbox-123')
      expect(sandbox.organization_id).to eq('org-1')
      expect(sandbox.user).to eq('daytona')
      expect(sandbox.state).to eq('started')
      expect(sandbox.cpu).to eq(4)
      expect(sandbox.memory).to eq(8)
      expect(sandbox.disk).to eq(30)
      expect(sandbox.last_activity_at).to eq('2025-01-01T00:00:00Z')
      expect(sandbox.network_block_all).to be(false)
      expect(sandbox.kvm).to be(false)
      expect(sandbox.queue_timeout).to be_nil
      expect(sandbox.queue_timed_out_at).to be_nil
      expect(sandbox.network_allow_list).to be_nil
      expect(sandbox.sandbox_class).to eq('linux-vm')
      expect(sandbox.warm_pool_id).to be_nil
      expect(sandbox.gpu_type).to eq('H100')
      expect(sandbox.desired_state).to eq('started')
      expect(sandbox.daemon_version).to eq('1.0.0')
      expect(sandbox.otel_endpoint_override).to eq('https://otel.example.com/')
    end

    it 'creates process, fs, git, computer_use, and code_interpreter helpers' do
      expect(sandbox.process).to be_a(Daytona::Process)
      expect(sandbox.fs).to be_a(Daytona::FileSystem)
      expect(sandbox.git).to be_a(Daytona::Git)
      expect(sandbox.computer_use).to be_a(Daytona::ComputerUse)
      expect(sandbox.code_interpreter).to be_a(Daytona::CodeInterpreter)
    end

    it 'configures toolbox client authorization and sdk headers' do
      described_class.new(sandbox_dto: sandbox_dto, config: config, sandbox_api: sandbox_api,
                          subscription_manager: subscription_manager)

      expect(toolbox_client.default_headers['Authorization']).to eq('Bearer test-api-key')
      expect(toolbox_client.default_headers['X-Daytona-Source']).to eq('sdk-ruby')
      expect(toolbox_client.default_headers['X-Daytona-SDK-Version']).to eq(Daytona::Sdk::VERSION)
      expect(toolbox_client).to have_received(:user_agent=).with("sdk-ruby/#{Daytona::Sdk::VERSION}").at_least(:once)
    end

    it 'adds organization header when using JWT auth' do
      jwt_config = Daytona::Config.new(jwt_token: 'jwt', organization_id: 'org-9', api_url: 'https://api.example.com')

      described_class.new(sandbox_dto: sandbox_dto, config: jwt_config, sandbox_api: sandbox_api,
                          subscription_manager: subscription_manager)

      expect(toolbox_client.default_headers['X-Daytona-Organization-ID']).to eq('org-9')
    end
  end

  describe '#archive' do
    it 'applies the sandbox the archive call returns without a refresh' do
      allow(sandbox_api).to receive(:archive_sandbox).with('sandbox-123')
                                                     .and_return(build_sandbox_dto(state: 'archived'))
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.archive

      expect(sandbox_api).to have_received(:archive_sandbox).with('sandbox-123')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(sandbox.state).to eq('archived')
    end

    it 'refreshes once when the response carries no state' do
      allow(sandbox_api).to receive(:archive_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: nil))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'archived'))

      sandbox.archive

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('archived')
    end

    it 'refreshes once when an older server returns no sandbox' do
      allow(sandbox_api).to receive(:archive_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'archived'))

      sandbox.archive

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('archived')
    end
  end

  describe '#public=' do
    [true, false].each do |value|
      it "sets public to #{value} via API" do
        allow(sandbox_api).to receive(:update_public_status).with('sandbox-123', value)

        sandbox.public = value

        expect(sandbox_api).to have_received(:update_public_status).with('sandbox-123', value)
        expect(sandbox.public).to eq(value)
      end
    end
  end

  describe '#auto_stop_interval=' do
    it 'sets interval via API' do
      allow(sandbox_api).to receive(:set_autostop_interval).with('sandbox-123', 30)

      sandbox.auto_stop_interval = 30

      expect(sandbox.auto_stop_interval).to eq(30)
    end

    it 'raises on negative interval' do
      expect { sandbox.auto_stop_interval = -1 }
        .to raise_error(Daytona::Sdk::Error, /Auto-stop interval must be a non-negative integer/)
    end
  end

  describe '#auto_pause_interval=' do
    it 'sets interval via API' do
      allow(sandbox_api).to receive(:set_auto_pause_interval).with('sandbox-123', 20)

      sandbox.auto_pause_interval = 20

      expect(sandbox.auto_pause_interval).to eq(20)
    end

    it 'raises on negative interval' do
      expect { sandbox.auto_pause_interval = -1 }
        .to raise_error(Daytona::Sdk::Error, /Auto-pause interval must be a non-negative integer/)
    end
  end

  describe '#auto_archive_interval=' do
    it 'sets interval via API' do
      allow(sandbox_api).to receive(:set_auto_archive_interval).with('sandbox-123', 60)

      sandbox.auto_archive_interval = 60

      expect(sandbox.auto_archive_interval).to eq(60)
    end

    it 'raises on negative interval' do
      expect { sandbox.auto_archive_interval = -1 }
        .to raise_error(Daytona::Sdk::Error, /Auto-archive interval must be a non-negative integer/)
    end
  end

  describe '#auto_delete_interval=' do
    it 'sets interval via API' do
      allow(sandbox_api).to receive(:set_auto_delete_interval).with('sandbox-123', 120)

      sandbox.auto_delete_interval = 120

      expect(sandbox.auto_delete_interval).to eq(120)
    end
  end

  describe '#ttl_minutes=' do
    it 'sets TTL via API' do
      allow(sandbox_api).to receive(:set_ttl).with('sandbox-123', 30)

      sandbox.ttl_minutes = 30

      expect(sandbox_api).to have_received(:set_ttl).with('sandbox-123', 30)
    end

    it 'raises on negative value' do
      expect { sandbox.ttl_minutes = -1 }
        .to raise_error(Daytona::Sdk::Error, /TTL must be a non-negative integer/)
    end
  end

  describe '#create_ssh_access' do
    it 'delegates to sandbox_api' do
      ssh_dto = instance_double(DaytonaApiClient::SshAccessDto)
      allow(sandbox_api).to receive(:create_ssh_access)
        .with('sandbox-123', { expires_in_minutes: 60 })
        .and_return(ssh_dto)

      expect(sandbox.create_ssh_access(60)).to eq(ssh_dto)
    end
  end

  describe '#delete' do
    it 'applies the sandbox the delete call returns without a refresh' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
                                                    .and_return(build_sandbox_dto(state: 'destroying'))
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.delete

      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(sandbox.state).to eq('destroying')
    end

    it 'refreshes once when the delete response carries no state' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: nil))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: 'destroying'))

      sandbox.delete

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('destroying')
    end

    it 'treats a 404 from the delete call as destroyed without a refresh' do
      allow(sandbox_api).to receive(:delete_sandbox).and_raise(DaytonaApiClient::ApiError.new(code: 404,
                                                                                              message: 'Not found'))
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.delete

      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(sandbox.state).to eq('destroyed')
    end

    it 'returns without blocking by default, refreshing once when an older server returns no sandbox' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: 'destroying'))

      sandbox.delete

      expect(sandbox_api).to have_received(:delete_sandbox).with('sandbox-123')
      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('destroying')
    end

    it 'sets state to destroyed when refresh returns 404' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).and_raise(
        DaytonaApiClient::ApiError.new(code: 404, message: 'Not found')
      )

      sandbox.delete

      expect(sandbox.state).to eq('destroyed')
    end

    it 're-raises non-404 API errors from delete call' do
      allow(sandbox_api).to receive(:delete_sandbox).and_raise(DaytonaApiClient::ApiError.new(code: 500,
                                                                                              message: 'boom'))

      expect { sandbox.delete }.to raise_error(DaytonaApiClient::ApiError)
    end

    it 're-raises non-404 API errors from post-delete refresh' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_raise(DaytonaApiClient::ApiError.new(code: 500,
                                                                                           message: 'Internal'))

      expect { sandbox.delete }.to raise_error(DaytonaApiClient::ApiError) { |e| expect(e.code).to eq(500) }
    end

    it 'blocks until destroyed with wait: true' do
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::DESTROYED))

      sandbox.delete(5, wait: true)

      expect(sandbox_api).to have_received(:delete_sandbox).with('sandbox-123')
      expect(sandbox.state).to eq('destroyed')
    end

    it 'tolerates transient 502 during wait: true polling' do
      stub_condition_variable_waits { |_seconds| nil }
      call_count = 0
      allow(sandbox_api).to receive(:delete_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        call_count += 1
        raise DaytonaApiClient::ApiError.new(code: 502, message: 'Bad Gateway') if call_count <= 2

        build_sandbox_dto(state: DaytonaApiClient::SandboxState::DESTROYED)
      end

      sandbox.delete(5, wait: true)

      expect(sandbox.state).to eq('destroyed')
    end
  end

  describe '#update_secrets' do
    it 'converts the hash to an array of single-entry hashes and refreshes state' do
      allow(sandbox_api).to receive(:update_sandbox_secrets)
        .and_return(build_sandbox_dto(env: { 'FOO' => 'placeholder' }))

      sandbox.update_secrets({ 'FOO' => 'foo-secret', 'BAR' => 'bar-secret' })

      expect(sandbox_api).to have_received(:update_sandbox_secrets) do |id, request|
        expect(id).to eq('sandbox-123')
        expect(request).to be_a(DaytonaApiClient::UpdateSandboxSecrets)
        expect(request.secrets).to eq([{ 'FOO' => 'foo-secret' }, { 'BAR' => 'bar-secret' }])
      end
      expect(sandbox.env).to eq({ 'FOO' => 'placeholder' })
    end

    it 'sends an empty array to detach all secrets' do
      allow(sandbox_api).to receive(:update_sandbox_secrets).and_return(build_sandbox_dto)

      sandbox.update_secrets({})

      expect(sandbox_api).to have_received(:update_sandbox_secrets) do |_id, request|
        expect(request.secrets).to eq([])
      end
    end
  end

  describe '#update_env' do
    it 'sends the set and unset values' do
      # The daemon responds with a status message, which update_env discards.
      allow(server_api).to receive(:update_env).and_return({ message: 'Environment updated successfully' })

      result = sandbox.update_env(env: { 'FOO' => 'bar' }, unset: ['OLD_VAR'])

      expect(result).to be_nil
      expect(server_api).to have_received(:update_env) do |request|
        expect(request).to be_a(DaytonaToolboxApiClient::UpdateEnvRequest)
        expect(request.set).to eq({ 'FOO' => 'bar' })
        expect(request.unset).to eq(['OLD_VAR'])
        expect(request.unset_value_prefix).to be_nil
      end
    end

    it 'wraps errors in Sdk::Error' do
      allow(server_api).to receive(:update_env).and_raise(StandardError, 'daemon unreachable')

      expect { sandbox.update_env(env: { 'FOO' => 'bar' }) }
        .to raise_error(Daytona::Sdk::Error, /Failed to update environment: daemon unreachable/)
    end
  end

  describe '#get_user_home_dir' do
    it 'returns the user home directory' do
      allow(info_api).to receive(:get_user_home_dir).and_return(double('DirResponse', dir: '/home/daytona'))

      expect(sandbox.get_user_home_dir).to eq('/home/daytona')
    end

    it 'wraps errors in Sdk::Error' do
      allow(info_api).to receive(:get_user_home_dir).and_raise(DaytonaApiClient::ApiError.new(code: 500,
                                                                                              message: 'connection refused'))

      expect { sandbox.get_user_home_dir }.to raise_error(Daytona::Sdk::Error, /Failed to get user home directory/)
    end
  end

  describe '#get_work_dir' do
    it 'returns the working directory' do
      allow(info_api).to receive(:get_work_dir).and_return(double('DirResponse', dir: '/workspace'))

      expect(sandbox.get_work_dir).to eq('/workspace')
    end

    it 'wraps errors in Sdk::Error' do
      allow(info_api).to receive(:get_work_dir).and_raise(DaytonaApiClient::ApiError.new(code: 500, message: 'timeout'))

      expect { sandbox.get_work_dir }.to raise_error(Daytona::Sdk::Error, /Failed to get working directory path/)
    end
  end

  describe '#labels=' do
    it 'replaces labels via API' do
      label_request = instance_double(DaytonaApiClient::SandboxLabels)
      label_response = instance_double(DaytonaApiClient::SandboxLabels, labels: { 'env' => 'prod' })
      allow(DaytonaApiClient::SandboxLabels)
        .to receive(:build_from_hash)
        .with(labels: { 'env' => 'prod' })
        .and_return(label_request)
      allow(sandbox_api).to receive(:replace_labels).with('sandbox-123', label_request).and_return(label_response)

      sandbox.labels = { 'env' => 'prod' }

      expect(sandbox.labels).to eq({ 'env' => 'prod' })
    end
  end

  describe '#preview_url' do
    it 'delegates to sandbox_api.get_port_preview_url' do
      preview = instance_double(DaytonaApiClient::PortPreviewUrl)
      allow(sandbox_api).to receive(:get_port_preview_url).with('sandbox-123', 3000).and_return(preview)

      expect(sandbox.preview_url(3000)).to eq(preview)
    end
  end

  describe '#create_signed_preview_url' do
    it 'delegates to sandbox_api with optional expiration' do
      signed_url = instance_double(DaytonaApiClient::SignedPortPreviewUrl)
      allow(sandbox_api).to receive(:get_signed_port_preview_url)
        .with('sandbox-123', 3000, { expires_in_seconds: 120 })
        .and_return(signed_url)

      expect(sandbox.create_signed_preview_url(3000, 120)).to eq(signed_url)
    end

    it 'passes nil expiration when not provided' do
      allow(sandbox_api).to receive(:get_signed_port_preview_url)
        .with('sandbox-123', 3000, { expires_in_seconds: nil })

      sandbox.create_signed_preview_url(3000)

      expect(sandbox_api).to have_received(:get_signed_port_preview_url)
    end
  end

  describe '#expire_signed_preview_url' do
    it 'delegates to sandbox_api' do
      allow(sandbox_api).to receive(:expire_signed_port_preview_url).with('sandbox-123', 3000, 'token-val')

      sandbox.expire_signed_preview_url(3000, 'token-val')

      expect(sandbox_api).to have_received(:expire_signed_port_preview_url).with('sandbox-123', 3000, 'token-val')
    end
  end

  describe '#refresh' do
    it 'updates state from API' do
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))

      sandbox.refresh

      expect(sandbox.state).to eq('stopped')
    end
  end

  describe '#refresh_activity' do
    it 'calls update_last_activity' do
      allow(sandbox_api).to receive(:update_last_activity).with('sandbox-123')

      expect(sandbox.refresh_activity).to be_nil
    end

    it 'wraps errors in Sdk::Error' do
      allow(sandbox_api).to receive(:update_last_activity).and_raise(DaytonaApiClient::ApiError.new(code: 500,
                                                                                                    message: 'offline'))

      expect { sandbox.refresh_activity }.to raise_error(Daytona::Sdk::Error, /Failed to refresh sandbox activity/)
    end
  end

  describe '#revoke_ssh_access' do
    it 'delegates to sandbox_api' do
      allow(sandbox_api).to receive(:revoke_ssh_access).with('sandbox-123', token: 'token-1')

      sandbox.revoke_ssh_access('token-1')

      expect(sandbox_api).to have_received(:revoke_ssh_access).with('sandbox-123', token: 'token-1')
    end
  end

  describe '#start' do
    it 'calls start_sandbox and waits for started state' do
      allow(sandbox_api).to receive(:start_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))

      sandbox.start(5)

      expect(sandbox_api).to have_received(:start_sandbox).with('sandbox-123')
    end
  end

  describe '#recover' do
    it 'calls recover_sandbox and waits for started state' do
      allow(sandbox_api).to receive(:recover_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))

      sandbox.recover(5)

      expect(sandbox_api).to have_received(:recover_sandbox).with('sandbox-123')
    end

    it 'wraps errors in Sdk::Error' do
      allow(sandbox_api).to receive(:recover_sandbox).and_raise(DaytonaApiClient::ApiError.new(code: 500,
                                                                                               message: 'bad state'))

      expect { sandbox.recover(5) }.to raise_error(Daytona::Sdk::Error, /Failed to recover sandbox/)
    end
  end

  describe '#stop' do
    it 'applies the stopped sandbox the stop call returns without a GET' do
      allow(sandbox_api).to receive(:stop_sandbox).with('sandbox-123', { force: false })
                                                  .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STOPPED))
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.stop(5)

      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(sandbox.state).to eq('stopped')
    end

    it 'refreshes exactly once when an older server returns no sandbox' do
      allow(sandbox_api).to receive(:stop_sandbox).with('sandbox-123', { force: false })
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STOPPED))

      sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('stopped')
    end

    it 'calls stop_sandbox and waits for stopped state' do
      allow(sandbox_api).to receive(:stop_sandbox).with('sandbox-123', { force: false })
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STOPPED))

      sandbox.stop(5)

      expect(sandbox_api).to have_received(:stop_sandbox).with('sandbox-123', { force: false })
    end

    it 'passes force stop options through to the API' do
      allow(sandbox_api).to receive(:stop_sandbox).with('sandbox-123', { force: true })
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::DESTROYED))

      sandbox.stop(5, force: true)

      expect(sandbox_api).to have_received(:stop_sandbox).with('sandbox-123', { force: true })
    end
  end

  describe '#resize' do
    it 'rejects per-node allocations' do
      expect do
        sandbox.resize(Daytona::Resources.new(memory_per_node: [8, 16]))
      end.to raise_error(Daytona::Sdk::Error, /[Pp]er-node/)
    end

    it 'raises error when resources is nil' do
      expect { sandbox.resize(nil) }.to raise_error(Daytona::Sdk::Error, /Resources must not be nil/)
    end

    it 'calls resize_sandbox with resource params' do
      allow(sandbox_api).to receive(:resize_sandbox).and_return(build_sandbox_dto(
                                                                  state: DaytonaApiClient::SandboxState::STARTED, cpu: 8
                                                                ))

      sandbox.resize(Daytona::Resources.new(cpu: 8, memory: 16, disk: 32), 5)

      expect(sandbox_api).to have_received(:resize_sandbox) do |_id, request|
        expect(request.cpu).to eq(8)
        expect(request.memory).to eq(16)
        expect(request.disk).to eq(32)
      end
    end

    it 'records the wait for the resize as wait_for_resize_complete' do
      allow(sandbox_api).to receive(:resize_sandbox)
        .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED, cpu: 8))
      allow(Daytona).to receive(:with_instrumentation).and_call_original

      sandbox.resize(Daytona::Resources.new(cpu: 8), 5)

      expect(Daytona).to have_received(:with_instrumentation).with(nil, 'Sandbox', 'wait_for_resize_complete')
    end

    it 'waits for the resize under the resize deadline when the Timeout.timeout exception is lost' do
      resizing = build_sandbox_dto(state: DaytonaApiClient::SandboxState::RESIZING)
      allow(sandbox_api).to receive(:resize_sandbox).and_return(resizing)
      # The poll and the refresh after the timeout see RESIZING. A third poll, which only a wait that ignores
      # the deadline makes, sees STARTED and ends the wait instead of hanging the spec.
      allow(sandbox_api).to receive(:get_sandbox)
        .and_return(resizing, resizing, build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      clock = stub_wait_clock

      expect { sandbox.resize(Daytona::Resources.new(cpu: 8), 0.2) }.to raise_error(
        Daytona::Sdk::TimeoutError, 'Sandbox sandbox-123 failed to resize within the 0.2 seconds timeout period'
      )
      # The subscribed poll interval is 1 s, so the wait was cut short at the deadline.
      expect(clock.call).to eq(0.2)
    end
  end

  describe '#create_lsp_server' do
    it 'returns an LspServer instance' do
      lsp = sandbox.create_lsp_server(language_id: :python, path_to_project: '/workspace')

      expect(lsp).to be_a(Daytona::LspServer)
      expect(lsp.language_id).to eq(:python)
      expect(lsp.path_to_project).to eq('/workspace')
    end
  end

  describe '#validate_ssh_access' do
    it 'delegates to the sandbox api' do
      validation = double('Validation')
      allow(sandbox_api).to receive(:validate_ssh_access).with('token-1').and_return(validation)

      expect(sandbox.validate_ssh_access('token-1')).to eq(validation)
    end
  end

  describe '#wait_for_sandbox_start' do
    it 'raises after one refresh when cached state is error' do
      errored = described_class.new(
        sandbox_dto: build_sandbox_dto(state: DaytonaApiClient::SandboxState::ERROR, error_reason: 'boom'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(
                                                               state: DaytonaApiClient::SandboxState::ERROR, error_reason: 'boom'
                                                             ))

      expect do
        errored.wait_for_sandbox_start
      end.to raise_error(Daytona::Sdk::Error,
                         /entered error state: error, error reason: boom/i)
    end

    it 'reports the error reason carried by the state event' do
      starting = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'starting'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      # The event arrives while a poll is in flight; the poll then returns the earlier state.
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        starting.send(:handle_event, 'sandbox.state.updated',
                      { 'sandbox' => { 'id' => 'sandbox-123', 'state' => 'error', 'desiredState' => 'started',
                                       'errorReason' => 'disk full' },
                        'oldState' => 'starting', 'newState' => 'error' })
        build_sandbox_dto(state: 'starting')
      end

      expect do
        starting.wait_for_sandbox_start
      end.to raise_error(Daytona::Sdk::Error, /entered error state: error, error reason: disk full/)
    end

    it 'clears the error reason on a state event without one' do
      errored = described_class.new(
        sandbox_dto: build_sandbox_dto(state: DaytonaApiClient::SandboxState::ERROR, error_reason: 'transient'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      errored.send(:handle_event, 'sandbox.state.updated',
                   { 'sandbox' => { 'id' => 'sandbox-123', 'state' => 'started' },
                     'oldState' => 'error', 'newState' => 'started' })

      expect(errored.state).to eq('started')
      expect(errored.error_reason).to be_nil
    end

    it 'raises QueueTimeoutError when the sandbox is destroyed after queue timeout' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(
        build_sandbox_dto(
          state: DaytonaApiClient::SandboxState::DESTROYED,
          queue_timeout: 15,
          queue_timed_out_at: '2026-09-29T07:00:00Z'
        )
      )

      expect do
        pending_sandbox.wait_for_sandbox_start
      end.to raise_error(
        Daytona::Sdk::QueueTimeoutError,
        'Sandbox sandbox-123 was destroyed after waiting 15 minutes for a runner ' \
        '(queue timed out at 2026-09-29T07:00:00Z)'
      )
    end

    it 'raises SpotEvictedError when the sandbox is destroyed by spot preemption' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(
        build_sandbox_dto(
          state: DaytonaApiClient::SandboxState::DESTROYED,
          spot_evicted_at: '2026-09-29T07:05:00Z'
        )
      )

      expect do
        pending_sandbox.wait_for_sandbox_start
      end.to raise_error(
        Daytona::Sdk::SpotEvictedError,
        'Sandbox sandbox-123 was evicted by spot preemption at 2026-09-29T07:05:00Z'
      )
    end

    it 'raises at the deadline when the Timeout.timeout exception is lost' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      pending_dto = build_sandbox_dto(state: 'pending')
      started_dto = build_sandbox_dto(state: 'started')
      # The poll and the refresh after the timeout see pending. A third poll, which only a wait that ignores
      # the deadline makes, sees started and ends the wait instead of hanging the spec.
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(pending_dto, pending_dto, started_dto)
      # As when the exception arrives while a request is inside libcurl: Timeout.timeout never interrupts.
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      clock = stub_wait_clock

      expect do
        pending_sandbox.wait_for_sandbox_start(0.2)
      end.to raise_error(
        Daytona::Sdk::TimeoutError, 'Sandbox sandbox-123 failed to start within the 0.2 seconds timeout period'
      )
      # The subscribed poll interval is 1 s, so the wait was cut short at the deadline.
      expect(clock.call).to eq(0.2)
    end

    context 'with the HTTP timeout of the poll requests' do
      let(:pending_sandbox) do
        described_class.new(
          sandbox_dto: build_sandbox_dto(state: 'pending'),
          config: config,
          sandbox_api: sandbox_api,
          subscription_manager: subscription_manager
        )
      end

      it 'sends each poll request through a copy of sandbox_api bounded by what is left of the timeout' do
        sandbox_api_client.default_headers['X-Custom'] = 'kept'
        bounded_api = instance_double(DaytonaApiClient::SandboxApi)
        allow(DaytonaApiClient::SandboxApi).to receive(:new).and_return(bounded_api)
        allow(bounded_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'started'))
        allow(sandbox_api).to receive(:get_sandbox)

        pending_sandbox.wait_for_sandbox_start(5)

        expect(DaytonaApiClient::SandboxApi).to have_received(:new).with(
          having_attributes(
            config: having_attributes(timeout: be_between(4, 5)),
            default_headers: include('X-Custom' => 'kept')
          )
        ).once
        expect(bounded_api).to have_received(:get_sandbox).once
        expect(sandbox_api).not_to have_received(:get_sandbox)
        expect(sandbox_api_client.config.timeout).to eq(0)
      end

      [5, Daytona::Sandbox::NO_TIMEOUT].each do |timeout|
        it "renews the wait's event subscription and records a refresh on each poll with timeout #{timeout}" do
          # A separate copy, as in production, so a poll through it does not take the #refresh shortcut.
          bounded_api = instance_double(DaytonaApiClient::SandboxApi)
          allow(DaytonaApiClient::SandboxApi).to receive(:new).and_return(bounded_api)
          polls = 0
          [sandbox_api, bounded_api].each do |api|
            allow(api).to receive(:get_sandbox).with('sandbox-123') do
              polls += 1
              build_sandbox_dto(state: polls >= 2 ? 'started' : 'pending')
            end
          end
          allow(Daytona).to receive(:with_instrumentation).and_call_original
          stub_condition_variable_waits { |_seconds| nil }

          pending_sandbox.wait_for_sandbox_start(timeout)

          expect(Daytona).to have_received(:with_instrumentation).with(nil, 'Sandbox', 'refresh').twice
          # Once after each poll; nothing renews it as the wait starts.
          expect(subscription_manager).to have_received(:refresh).with('sub-123').twice
        end
      end

      it 'keeps a shorter timeout of the API client' do
        sandbox_api_client.config.timeout = 2
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'started'))

        pending_sandbox.wait_for_sandbox_start(5)

        expect(DaytonaApiClient::SandboxApi).to have_received(:new)
          .with(having_attributes(config: having_attributes(timeout: 2))).once
      end

      it 'keeps the request error when a shorter timeout of the API client fires before the deadline' do
        sandbox_api_client.config.timeout = 2
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                   .and_raise(DaytonaApiClient::ApiError.new('Connection timed out'))

        expect do
          pending_sandbox.wait_for_sandbox_start(60)
        end.to raise_error(DaytonaApiClient::ApiError, 'Connection timed out')
      end

      it 'bounds the poll requests by the deadline when the API client timeout is nil' do
        sandbox_api_client.config.timeout = nil
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'started'))

        pending_sandbox.wait_for_sandbox_start(5)

        expect(DaytonaApiClient::SandboxApi).to have_received(:new)
          .with(having_attributes(config: having_attributes(timeout: be_between(4, 5)))).once
      end

      it 'polls without an HTTP timeout when timeout is NO_TIMEOUT' do
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'started'))

        pending_sandbox.wait_for_sandbox_start(Daytona::Sandbox::NO_TIMEOUT)

        expect(DaytonaApiClient::SandboxApi).not_to have_received(:new)
      end

      it 'raises a TimeoutError when a poll request times out, after a final refresh bounded by 5 s' do
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                   .and_raise(DaytonaApiClient::ApiError.new('Connection timed out'))

        expect do
          pending_sandbox.wait_for_sandbox_start(30)
        end.to raise_error(
          Daytona::Sdk::TimeoutError, 'Sandbox sandbox-123 failed to start within the 30 seconds timeout period'
        )
        expect(sandbox_api).to have_received(:get_sandbox).twice
        expect(DaytonaApiClient::SandboxApi).to have_received(:new)
          .with(having_attributes(config: having_attributes(timeout: 5.0))).once
      end
    end

    it 'recovers from stale cached ERROR when refresh returns started' do
      errored = described_class.new(
        sandbox_dto: build_sandbox_dto(state: DaytonaApiClient::SandboxState::ERROR, error_reason: 'stale'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))

      expect { errored.wait_for_sandbox_start }.not_to raise_error
      expect(errored.state).to eq('started')
    end

    it 'uses origin/main polling cadence when no event subscription is active' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      condition = instance_double(ConditionVariable, signal: nil)
      waits = []
      refresh_count = 0

      allow(subscription_manager).to receive(:refresh).and_return(false)
      allow(subscription_manager).to receive(:subscribe).and_return(nil)
      allow(ConditionVariable).to receive(:new).and_return(condition)
      allow(condition).to receive(:wait) do |_mutex, interval|
        waits << interval
      end
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        refresh_count += 1
        build_sandbox_dto(state: refresh_count >= 2 ? DaytonaApiClient::SandboxState::STARTED : 'pending')
      end

      pending_sandbox.wait_for_sandbox_start

      expect(waits.first).to eq(0.1)
    end

    it 'keeps a sparse 5s polling safety net when an event subscription is active' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      condition = instance_double(ConditionVariable, signal: nil)
      waits = []
      refresh_count = 0

      allow(subscription_manager).to receive(:refresh).and_return(true)
      allow(ConditionVariable).to receive(:new).and_return(condition)
      allow(condition).to receive(:wait) do |_mutex, interval|
        waits << interval
      end
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        refresh_count += 1
        build_sandbox_dto(state: refresh_count >= 2 ? DaytonaApiClient::SandboxState::STARTED : 'pending')
      end

      pending_sandbox.wait_for_sandbox_start

      expect(waits.first).to eq(5.0)
    end

    it 'enforces timeout (deliberate keep)' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: 'pending'))

      expect do
        pending_sandbox.wait_for_sandbox_start(0.15)
      end.to raise_error(Daytona::Sdk::TimeoutError)
    end

    it 'near-zero timeout still performs one refresh before raising' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))

      expect { pending_sandbox.wait_for_sandbox_start(0.001) }.not_to raise_error
      expect(pending_sandbox.state).to eq('started')
    end

    it 'preserves original TimeoutError when final refresh raises ApiError' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      refresh_count = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        refresh_count += 1
        raise DaytonaApiClient::ApiError.new(code: 502, message: 'Bad Gateway') if refresh_count >= 2

        build_sandbox_dto(state: 'pending')
      end

      expect do
        pending_sandbox.wait_for_sandbox_start(0.15)
      end.to raise_error(Daytona::Sdk::TimeoutError, /failed to start/) { |error|
        expect(error.sandbox_id).to be_nil
      }
    end

    it 'raises Sdk::Error when final refresh discovers error state' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      refresh_count = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        refresh_count += 1
        build_sandbox_dto(state: refresh_count >= 2 ? DaytonaApiClient::SandboxState::ERROR : 'pending')
      end

      expect do
        pending_sandbox.wait_for_sandbox_start(0.15)
      end.to raise_error(Daytona::Sdk::Error, /entered error state/)
    end

    it 'returns normally when final refresh reaches target state at timeout' do
      pending_sandbox = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      refresh_count = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        refresh_count += 1
        build_sandbox_dto(state: refresh_count >= 2 ? DaytonaApiClient::SandboxState::STARTED : 'pending')
      end

      expect { pending_sandbox.wait_for_sandbox_start(0.15) }.not_to raise_error
      expect(pending_sandbox.state).to eq('started')
    end
  end

  describe '#wait_for_sandbox_stop' do
    it 'returns immediately when already destroyed' do
      destroyed = described_class.new(
        sandbox_dto: build_sandbox_dto(state: DaytonaApiClient::SandboxState::DESTROYED),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )

      expect { destroyed.wait_for_sandbox_stop }.not_to raise_error
    end

    it 'treats destroyed as a successful target state even when queue timeout markers are present' do
      stopping = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'stopping'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(
        build_sandbox_dto(
          state: DaytonaApiClient::SandboxState::DESTROYED,
          queue_timeout: 15,
          queue_timed_out_at: '2026-09-29T07:00:00Z'
        )
      )

      expect { stopping.wait_for_sandbox_stop }.not_to raise_error
      expect(stopping.state).to eq('destroyed')
    end

    it 'enforces timeout (deliberate keep)' do
      stopping = described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'stopping'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: 'stopping'))

      expect do
        stopping.wait_for_sandbox_stop(0.15)
      end.to raise_error(Daytona::Sdk::TimeoutError) { |error| expect(error.sandbox_id).to be_nil }
    end
  end

  describe '#pause' do
    it 'applies the paused sandbox the pause call returns without a GET' do
      allow(sandbox_api).to receive(:pause_sandbox).with('sandbox-123')
                                                   .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::PAUSED))
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.pause(timeout: 5)

      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(sandbox.state).to eq('paused')
    end

    it 'refreshes exactly once when an older server returns no sandbox' do
      allow(sandbox_api).to receive(:pause_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::PAUSED))

      sandbox.pause(timeout: 5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(sandbox.state).to eq('paused')
    end

    it 'resolves when landing in stopped state (left-pausing semantics)' do
      pausing = described_class.new(
        sandbox_dto: build_sandbox_dto(state: DaytonaApiClient::SandboxState::PAUSING),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
      allow(sandbox_api).to receive(:pause_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STOPPED))

      expect { pausing.pause(timeout: 5) }.not_to raise_error
      expect(pausing.state).to eq('stopped')
    end

    it 'resolves when landing in paused state' do
      allow(sandbox_api).to receive(:pause_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::PAUSED))

      expect { sandbox.pause(timeout: 5) }.not_to raise_error
      expect(sandbox.state).to eq('paused')
    end

    it 'raises on error state during pause' do
      allow(sandbox_api).to receive(:pause_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(
                                                               state: DaytonaApiClient::SandboxState::ERROR, error_reason: 'oom'
                                                             ))

      expect { sandbox.pause(timeout: 5) }.to raise_error(Daytona::Sdk::Error, /error state/)
    end
  end

  describe '#create_snapshot' do
    it 'creates a snapshot and waits for completion' do
      allow(sandbox_api).to receive(:create_sandbox_snapshot).with('sandbox-123', anything)
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: DaytonaApiClient::SandboxState::STARTED))

      sandbox.create_snapshot(name: 'snap-1', timeout: 5)

      expect(sandbox_api).to have_received(:create_sandbox_snapshot) do |_id, request|
        expect(request.name).to eq('snap-1')
      end
    end
  end

  describe '#fork' do
    it 'forks the sandbox and returns the forked sandbox' do
      forked_dto = build_sandbox_dto(id: 'forked-456', state: DaytonaApiClient::SandboxState::STARTED)
      allow(sandbox_api).to receive(:fork_sandbox).with('sandbox-123', anything).and_return(forked_dto)
      allow(sandbox_api).to receive(:get_sandbox).with('forked-456').and_return(forked_dto)

      result = sandbox.fork(name: 'my-fork', timeout: 5)

      expect(result).to be_a(Daytona::Sandbox)
      expect(result.id).to eq('forked-456')
      expect(sandbox_api).to have_received(:fork_sandbox) do |_id, request|
        expect(request.name).to eq('my-fork')
      end
    end

    it 'records the wait for the fork to start as wait_for_sandbox_start' do
      forked_dto = build_sandbox_dto(id: 'forked-456', state: DaytonaApiClient::SandboxState::STARTED)
      allow(sandbox_api).to receive(:fork_sandbox).with('sandbox-123', anything).and_return(forked_dto)
      allow(sandbox_api).to receive(:get_sandbox).with('forked-456').and_return(forked_dto)
      allow(Daytona).to receive(:with_instrumentation).and_call_original

      sandbox.fork(timeout: 5)

      expect(Daytona).to have_received(:with_instrumentation).with(nil, 'Sandbox', 'wait_for_sandbox_start')
    end

    it 'waits for the fork to start under the fork deadline when the Timeout.timeout exception is lost' do
      forked_dto = build_sandbox_dto(id: 'forked-456', state: 'pending')
      allow(sandbox_api).to receive(:fork_sandbox).with('sandbox-123', anything).and_return(forked_dto)
      started_dto = build_sandbox_dto(id: 'forked-456', state: 'started')
      # The poll and the refresh after the timeout see pending. A third poll, which only a wait that ignores
      # the deadline makes, sees started and ends the wait instead of hanging the spec.
      allow(sandbox_api).to receive(:get_sandbox).with('forked-456').and_return(forked_dto, forked_dto, started_dto)
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      clock = stub_wait_clock

      expect { sandbox.fork(timeout: 0.2) }.to raise_error(
        Daytona::Sdk::TimeoutError,
        'Sandbox sandbox-123 fork failed to become ready within the 0.2 seconds timeout period'
      )
      # The subscribed poll interval is 1 s, so the wait was cut short at the deadline.
      expect(clock.call).to eq(0.2)
    end
  end

  describe '#experimental_create_snapshot (deprecated)' do
    it 'delegates to #create_snapshot and emits a deprecation warning' do
      allow(sandbox).to receive(:create_snapshot)

      expect { sandbox.experimental_create_snapshot(name: 'snap-1', timeout: 5) }
        .to output(/DEPRECATION.*experimental_create_snapshot/).to_stderr

      expect(sandbox).to have_received(:create_snapshot).with(name: 'snap-1', timeout: 5)
    end
  end

  describe '#experimental_fork (deprecated)' do
    it 'delegates to #fork and emits a deprecation warning' do
      allow(sandbox).to receive(:fork)

      expect { sandbox.experimental_fork(name: 'my-fork', timeout: 5) }
        .to output(/DEPRECATION.*experimental_fork/).to_stderr

      expect(sandbox).to have_received(:fork).with(name: 'my-fork', timeout: 5)
    end
  end

  describe 'state event subscriptions' do
    let(:pending_sandbox) do
      described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
    end

    def stub_polls(*states)
      dtos = states.map { |state| build_sandbox_dto(state:) }
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(*dtos)
    end

    it 'does not subscribe when constructed or on ordinary method calls' do
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'archived'))
      allow(sandbox_api).to receive(:archive_sandbox).with('sandbox-123')
      allow(sandbox_api).to receive(:update_last_activity).with('sandbox-123')

      sandbox.refresh
      sandbox.archive
      sandbox.refresh_activity

      expect(subscription_manager).not_to have_received(:subscribe)
      expect(subscription_manager).not_to have_received(:refresh)
    end

    it 'subscribes to state events only for the wait and unsubscribes when it succeeds' do
      stub_polls('pending', 'started')
      stub_condition_variable_waits { |_seconds| nil }

      pending_sandbox.wait_for_sandbox_start

      expect(subscription_manager).to have_received(:subscribe)
        .with(resource_id: 'sandbox-123', handler: kind_of(Method), events: ['sandbox.state.updated']).once
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'neither subscribes nor polls when the cached state already is the target' do
      allow(sandbox_api).to receive(:get_sandbox)

      sandbox.wait_for_sandbox_start

      expect(subscription_manager).not_to have_received(:subscribe)
      expect(sandbox_api).not_to have_received(:get_sandbox)
    end

    it 'unsubscribes when the wait ends in an error state' do
      stub_polls('error')

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(Daytona::Sdk::Error, /entered error state/)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'unsubscribes when the wait times out' do
      stub_polls('pending')
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      stub_wait_clock

      expect { pending_sandbox.wait_for_sandbox_start(0.2) }.to raise_error(Daytona::Sdk::TimeoutError)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'unsubscribes when a poll raises' do
      allow(sandbox_api).to receive(:get_sandbox).and_raise(DaytonaApiClient::ApiError.new(code: 500, message: 'boom'))

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(DaytonaApiClient::ApiError)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'unsubscribes when the waiting thread is interrupted' do
      cancelled = Class.new(Exception) # rubocop:disable Lint/InheritException
      allow(sandbox_api).to receive(:get_sandbox).and_raise(cancelled)

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(cancelled)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'gives concurrent waits on the same sandbox their own subscriptions' do
      allow(subscription_manager).to receive(:subscribe).and_return('sub-a', 'sub-b')
      first_polled = Queue.new
      release = Queue.new
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        if Thread.current[:first_wait]
          first_polled << true
          release.pop
        end
        build_sandbox_dto(state: 'started')
      end

      first = Thread.new do
        Thread.current[:first_wait] = true
        pending_sandbox.wait_for_sandbox_start
      end
      first_polled.pop
      # The second wait ends while the first one is still waiting.
      pending_sandbox.wait_for_sandbox_start
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-b').once
      expect(subscription_manager).not_to have_received(:unsubscribe).with('sub-a')
      release << true
      first.join

      expect(subscription_manager).to have_received(:unsubscribe).with('sub-a').once
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-b').once
    end

    it 'subscribes again when the wait subscription expired, and releases the new one' do
      allow(subscription_manager).to receive(:subscribe).and_return('sub-1', 'sub-2')
      allow(subscription_manager).to receive(:refresh).with('sub-1').and_return(false)
      allow(subscription_manager).to receive(:refresh).with('sub-2').and_return(true)
      stub_polls('pending', 'pending', 'started')
      stub_condition_variable_waits { |_seconds| nil }

      pending_sandbox.wait_for_sandbox_start

      expect(subscription_manager).to have_received(:subscribe).twice
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-2').once
      expect(subscription_manager).not_to have_received(:unsubscribe).with('sub-1')
    end

    it 'polls every second until the event connection is up, sparsely while it is, and every second once it drops' do
      allow(subscription_manager).to receive(:event_backed?).and_return(false, true, false)
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      stub_polls('pending', 'pending', 'pending', 'started')
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      pending_sandbox.wait_for_sandbox_start

      expect(waits).to eq([1.0, 5.0, 1.0])
    end

    describe 'poll interval' do
      def interval(subscription_id:, random:)
        allow(sandbox).to receive(:poll_jitter_random).and_return(random)
        sandbox.send(:next_poll_interval, subscription: { id: subscription_id }, fast_poll_interval: 0.1)
      end

      it 'is 5 s with up to 20 % jitter while the wait is event-backed' do
        expect([0.0, 0.5, 1.0].map { |r| interval(subscription_id: 'sub-123', random: r) })
          .to eq([4.0, 5.0, 6.0])
      end

      it 'is 1 s with up to 20 % jitter while subscribed but disconnected' do
        allow(subscription_manager).to receive(:event_backed?).and_return(false)

        expect([0.0, 0.5, 1.0].map { |r| interval(subscription_id: 'sub-123', random: r) })
          .to eq([0.8, 1.0, 1.2])
      end

      # The fallback intervals of one wait that is not event-backed, at a fixed jitter draw.
      def fallback_sequence(random, count)
        allow(subscription_manager).to receive(:event_backed?).and_return(false)
        allow(sandbox).to receive(:poll_jitter_random).and_return(random)
        backoff = { interval: 1.0 }
        Array.new(count) do
          sandbox.send(:next_poll_interval, subscription: { id: 'sub-123' }, fast_poll_interval: 0.1, backoff:)
        end
      end

      def about(*values) = values.map { |v| be_within(1e-9).of(v) }

      it 'backs off x1.5 from 1 s to a 10 s cap while not event-backed, with up to 20 % jitter' do
        expect(fallback_sequence(0.5, 8)).to match(about(1, 1.5, 2.25, 3.375, 5.0625, 7.59375, 10, 10))
        expect(fallback_sequence(0.0, 8)).to match(about(0.8, 1.2, 1.8, 2.7, 4.05, 6.075, 8, 8))
        expect(fallback_sequence(1.0, 8)).to match(about(1.2, 1.8, 2.7, 4.05, 6.075, 9.1125, 12, 12))
      end

      it 'starts the backoff over after a stretch of being event-backed' do
        allow(subscription_manager).to receive(:event_backed?).and_return(false, false, false, true, true, false, false)
        allow(sandbox).to receive(:poll_jitter_random).and_return(0.5)
        backoff = { interval: 1.0 }
        intervals = Array.new(7) do
          sandbox.send(:next_poll_interval, subscription: { id: 'sub-123' }, fast_poll_interval: 0.1, backoff:)
        end

        expect(intervals).to eq([1.0, 1.5, 2.25, 5.0, 5.0, 1.0, 1.5])
      end

      it 'is the fast cadence, without jitter, when the wait holds no subscription' do
        allow(sandbox).to receive(:poll_jitter_random)

        expect(sandbox.send(:next_poll_interval, subscription: { id: nil }, fast_poll_interval: 0.1)).to eq(0.1)
        expect(sandbox.send(:next_poll_interval, subscription: { id: nil }, fast_poll_interval: 0.11)).to eq(0.11)
        expect(sandbox).not_to have_received(:poll_jitter_random)
      end
    end

    # Records the stubbed wait clock at every poll.
    def poll_times_until_started(polls_before_start)
      clock = stub_wait_clock
      times = []
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        times << clock.call
        build_sandbox_dto(state: times.size > polls_before_start ? 'started' : 'pending')
      end
      pending_sandbox.wait_for_sandbox_start(60)
      times
    end

    it 'makes no poll between the first one and 4 s while event-backed, and polls again by 6 s' do
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.0, 1.0)

      expect(poll_times_until_started(2)).to eq([0.0, 4.0, 10.0])
    end

    it 'backs off from 1 s, not every 100 ms, while subscribed but disconnected' do
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      allow(subscription_manager).to receive(:event_backed?).and_return(false)
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)

      # Gaps of 1, 1.5, 2.25, 3.375, 5.0625, 7.59375, then the 10 s cap.
      expect(poll_times_until_started(7)).to eq([0.0, 1.0, 2.5, 4.75, 8.125, 13.1875, 20.78125, 30.78125])
    end

    it 'starts the backoff over at 1 s after being event-backed' do
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      allow(subscription_manager).to receive(:event_backed?).and_return(false, false, true, false)
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)

      expect(poll_times_until_started(4)).to eq([0.0, 1.0, 2.5, 7.5, 8.5])
    end

    def rate_limited(headers = {})
      DaytonaApiClient::ApiError.new(code: 429, response_headers: headers, message: 'Too Many Requests')
    end

    # Records the stubbed wait clock at every poll; the first poll fails with +error+.
    # A long timeout, so the deadline never caps the interval under test.
    def poll_times_after_failure(error)
      clock = stub_wait_clock
      times = []
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        times << clock.call
        raise error if times.size == 1

        build_sandbox_dto(state: 'started')
      end
      pending_sandbox.wait_for_sandbox_start(600)
      times
    end

    context 'when a poll is rate limited' do
      before do
        allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
        allow(subscription_manager).to receive(:event_backed?).and_return(false)
        allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      end

      it 'polls again after Retry-After seconds, plus up to 20 %, and still resolves' do
        expect(poll_times_after_failure(rate_limited('Retry-After' => '3'))).to match([0.0, be_within(1e-9).of(3.3)])
        expect(pending_sandbox.state).to eq('started')
      end

      it 'spreads Retry-After upwards only, by up to 20 %' do
        allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.0)
        expect(poll_times_after_failure(rate_limited('Retry-After' => '3'))).to eq([0.0, 3.0])
      end

      it 'spreads Retry-After by at most 20 %' do
        allow(pending_sandbox).to receive(:poll_jitter_random).and_return(1.0)
        expect(poll_times_after_failure(rate_limited('Retry-After' => '3'))).to match([0.0, be_within(1e-9).of(3.6)])
      end

      it 'caps Retry-After at 60 s before spreading it' do
        allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.0)
        expect(poll_times_after_failure(rate_limited('Retry-After' => '999999999'))).to eq([0.0, 60.0])
      end

      it 'honours a Retry-After HTTP date' do
        times = poll_times_after_failure(rate_limited('retry-after' => (Time.now + 4).httpdate))

        expect(times.last).to be_between(2.5, 5.0)
      end

      %w[inf -inf Infinity nan -5 1.5 soon].each do |value|
        it "treats Retry-After #{value} as absent" do
          expect(poll_times_after_failure(rate_limited('Retry-After' => value))).to eq([0.0, 1.0])
        end
      end

      it 'polls again after the current interval without Retry-After' do
        expect(poll_times_after_failure(rate_limited)).to eq([0.0, 1.0])
      end

      it 'never polls sooner than the current interval' do
        expect(poll_times_after_failure(rate_limited('Retry-After' => '0'))).to eq([0.0, 1.0])
      end
    end

    it 'still fails the wait on a server error' do
      allow(sandbox_api).to receive(:get_sandbox).and_raise(DaytonaApiClient::ApiError.new(code: 500, message: 'boom'))

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(DaytonaApiClient::ApiError) { |e|
        expect(e.code).to eq(500)
      }
      expect(sandbox_api).to have_received(:get_sandbox).once
    end

    it 'backs off on repeated 429s even without a subscription' do
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      allow(subscription_manager).to receive(:subscribe).and_return(nil)
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      clock = stub_wait_clock
      times = []
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        times << clock.call
        if times.size <= 3
          raise DaytonaApiClient::ApiError.new(code: 429, response_headers: {}, message: 'Too Many Requests')
        end

        build_sandbox_dto(state: 'started')
      end

      pending_sandbox.wait_for_sandbox_start(60)

      expect(times).to eq([0.0, 1.0, 2.5, 4.75])
    end

    describe 'retry_after_seconds' do
      it 'reads whole seconds and HTTP dates, caps at 60 s, and treats the rest as absent' do
        table = {
          nil => 0.0, '3' => 3.0, ' 7 ' => 7.0, '60' => 60.0, '61' => 60.0, '99999999999999999999' => 60.0,
          '0' => 0.0, '-5' => 0.0, 'inf' => 0.0, '-inf' => 0.0, 'Infinity' => 0.0, 'nan' => 0.0, '1.5' => 0.0,
          'soon' => 0.0, (Time.now + 7200).httpdate => 60.0, (Time.now - 3600).httpdate => 0.0
        }
        table.each do |value, want|
          headers = value.nil? ? {} : { 'Retry-After' => value }
          expect(pending_sandbox.send(:retry_after_seconds, headers)).to eq(want), "Retry-After #{value.inspect}"
        end
      end
    end

    it 'polls fast when no subscription could be created' do
      allow(subscription_manager).to receive(:subscribe).and_return(nil)
      stub_polls('pending', 'started')
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      pending_sandbox.wait_for_sandbox_start

      expect(waits).to eq([0.1])
      expect(subscription_manager).not_to have_received(:event_backed?)
      expect(subscription_manager).not_to have_received(:unsubscribe)
    end

    it 'ends the wait on a slim state event and applies only the state' do
      handler = nil
      allow(subscription_manager).to receive(:subscribe) do |**kwargs|
        handler = kwargs[:handler]
        'sub-123'
      end
      allow(subscription_manager).to receive(:refresh) do
        handler.call('sandbox.state.updated',
                     { 'sandbox' => { 'id' => 'sandbox-123', 'state' => 'started', 'cpu' => 99 },
                       'oldState' => 'pending', 'newState' => 'started' })
        true
      end
      stub_polls('pending')

      pending_sandbox.wait_for_sandbox_start

      expect(pending_sandbox.state).to eq('started')
      expect(pending_sandbox.cpu).to eq(4)
      expect(sandbox_api).to have_received(:get_sandbox).once
    end
  end

  describe 'operations that subscribe before their request' do
    let(:started_sandbox) do
      described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'started'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: subscription_manager
      )
    end
    let(:handler) { [] }

    before do
      allow(subscription_manager).to receive(:subscribe) do |**kwargs|
        handler << kwargs[:handler]
        'sub-123'
      end
      allow(sandbox_api).to receive(:get_sandbox)
    end

    def state_event(new_state)
      handler.last.call('sandbox.state.updated', { 'sandbox' => { 'id' => 'sandbox-123', 'state' => new_state } })
    end

    # Sends a state event once the wait blocks between polls, from another thread as the event connection does.
    def emit_when_waiting(new_state)
      sent = false
      allow(ConditionVariable).to receive(:new).and_wrap_original do |original|
        original.call.tap do |cv|
          allow(cv).to receive(:wait).and_wrap_original do |wait, mutex, seconds|
            Thread.new { state_event(new_state) } unless sent
            sent = true
            wait.call(mutex, seconds)
          end
        end
      end
    end

    it 'reads no extra time when the rejection arrives during a read, and then polls at the fallback cadence' do
      pending_sandbox = described_class.new(sandbox_dto: build_sandbox_dto(state: 'pending'), config:, sandbox_api:,
                                            subscription_manager:)
      rejected = false
      allow(subscription_manager).to receive(:event_backed?) { !rejected }
      allow(pending_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      gets = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        gets += 1
        if gets == 1
          rejected = true
          pending_sandbox.send(:handle_event, Daytona::EventDispatcher::SUBSCRIPTION_REJECTED_EVENT, nil)
        end
        build_sandbox_dto(state: gets >= 2 ? 'started' : 'pending')
      end
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      pending_sandbox.wait_for_sandbox_start

      expect(gets).to eq(2)
      expect(waits).to eq([1.0])
    end

    it 'stop: a rate-limited first read does not fail on the cached error state of the response' do
      allow(Timeout).to receive(:timeout) { |*_args, &block| block.call }
      stub_wait_clock
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'error'))
      gets = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        gets += 1
        raise DaytonaApiClient::ApiError.new(code: 429, response_headers: {}, message: 'Too Many Requests') if gets == 1

        build_sandbox_dto(state: 'stopped')
      end

      started_sandbox.stop(30)

      expect(gets).to eq(2)
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'stop: reads at once when the subscription is rejected while waiting, then polls at the fallback cadence' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      gets = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        gets += 1
        build_sandbox_dto(state: 'stopping')
      end
      allow(started_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      rejected = false
      allow(subscription_manager).to receive(:event_backed?) { !rejected }
      intervals = []
      gets_at_wait = []
      allow(ConditionVariable).to receive(:new).and_wrap_original do |original|
        original.call.tap do |cv|
          allow(cv).to receive(:wait).and_wrap_original do |wait, mutex, seconds|
            intervals << seconds
            gets_at_wait << gets
            if intervals.size == 1
              Thread.new do
                rejected = true
                handler.last.call(Daytona::EventDispatcher::SUBSCRIPTION_REJECTED_EVENT, nil)
              end
            else
              Thread.new { state_event('stopped') }
            end
            wait.call(mutex, seconds)
          end
        end
      end

      elapsed = Benchmark.realtime { started_sandbox.stop(30) }

      expect(elapsed).to be < 2.5
      expect(intervals).to eq([5.0, 1.0])
      expect(gets_at_wait).to eq([0, 1])
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'stop: skips the first read when the subscription was live, and ends on the event' do
      allow(sandbox_api).to receive(:stop_sandbox) do
        expect(subscription_manager).to have_received(:subscribe).once
        build_sandbox_dto(state: 'stopping')
      end
      emit_when_waiting('stopped')

      started_sandbox.stop(5)

      expect(started_sandbox.state).to eq('stopped')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'stop: reads once at once when the event connection was down at the request' do
      allow(subscription_manager).to receive(:event_backed?).and_return(false)
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'stop: reads once at once when a state event arrived during the request' do
      allow(sandbox_api).to receive(:stop_sandbox) do
        state_event('stopped')
        build_sandbox_dto(state: 'stopping')
      end
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'stop: reads once at once when the event connection reconnected during the request' do
      epoch = 0
      allow(subscription_manager).to receive(:connection_epoch) { epoch }
      allow(sandbox_api).to receive(:stop_sandbox) do
        # The socket dropped and came back while the request ran; the stopped event was lost.
        epoch += 1
        build_sandbox_dto(state: 'stopping')
      end
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'delete(wait: true): reads at once when the response is empty and the fallback refresh fails' do
      allow(sandbox_api).to receive(:delete_sandbox)
      gets = 0
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123') do
        gets += 1
        raise DaytonaApiClient::ApiError.new(code: 502, message: 'Bad Gateway') if gets == 1

        build_sandbox_dto(state: 'destroyed')
      end
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.delete(5, wait: true)

      expect(gets).to eq(2)
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('destroyed')
    end

    it 'delete(wait: true): skips the first read when the fallback refresh of an empty response succeeds' do
      allow(sandbox_api).to receive(:delete_sandbox)
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'destroying'))
      emit_when_waiting('destroyed')

      started_sandbox.delete(5, wait: true)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(started_sandbox.state).to eq('destroyed')
    end

    it 'stop: reads once at once when a state event arrives while the subscription is taken' do
      allow(subscription_manager).to receive(:subscribe) do |**kwargs|
        handler << kwargs[:handler]
        # Delivered as soon as the subscription exists, before the (older) stop response is applied.
        state_event('stopped')
        'sub-123'
      end
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('stopped')
    end

    # A response without a state is treated as an empty body: the refresh it falls back to, or the wait's
    # immediate read, supplies the state.
    def stateless_dto(sandbox_id = 'sandbox-123') = build_sandbox_dto(id: sandbox_id, state: nil)

    {
      'stop' => [:stop_sandbox, 'stopped', ->(sandbox) { sandbox.stop(5) }],
      'pause' => [:pause_sandbox, 'paused', ->(sandbox) { sandbox.pause(timeout: 5) }],
      'resize' => [:resize_sandbox, 'started', ->(sandbox) { sandbox.resize(Daytona::Resources.new(cpu: 4), 5) }],
      'delete(wait: true)' => [:delete_sandbox, 'destroyed', ->(sandbox) { sandbox.delete(5, wait: true) }]
    }.each do |operation, (api_method, final_state, run)|
      it "#{operation}: refreshes once, without waiting, when the response carries no state" do
        allow(sandbox_api).to receive(api_method).and_return(stateless_dto)
        allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                   .and_return(build_sandbox_dto(state: final_state))
        waits = []
        stub_condition_variable_waits { |seconds| waits << seconds }

        run.call(started_sandbox)

        expect(sandbox_api).to have_received(:get_sandbox).once
        expect(waits).to be_empty
        expect(started_sandbox.state).to eq(final_state)
      end
    end

    it 'resize: refreshes once, then waits, when the response is empty' do
      allow(sandbox_api).to receive(:resize_sandbox)
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'resizing'))
      emit_when_waiting('started')

      started_sandbox.resize(Daytona::Resources.new(cpu: 4), 5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(started_sandbox.state).to eq('started')
    end

    it 'fork: reads once at once when the toolbox proxy URL had to be fetched' do
      allow(sandbox_api).to receive(:fork_sandbox)
        .and_return(build_sandbox_dto(id: 'fork-1', state: 'starting', toolbox_proxy_url: ''))
      allow(sandbox_api).to receive(:get_toolbox_proxy_url).with('fork-1')
                                                           .and_return(double('ToolboxProxyUrl', url: 'https://proxy.example.com'))
      allow(sandbox_api).to receive(:get_sandbox).with('fork-1')
                                                 .and_return(build_sandbox_dto(id: 'fork-1', state: 'starting'))
      emit_when_waiting('started')

      forked = started_sandbox.fork(timeout: 5)

      expect(forked.state).to eq('started')
      expect(sandbox_api).to have_received(:get_toolbox_proxy_url).once
      expect(sandbox_api).to have_received(:get_sandbox).with('fork-1').once
    end

    it 'fork: reads at once when the response carries no state' do
      allow(sandbox_api).to receive(:fork_sandbox).and_return(stateless_dto('fork-1'))
      allow(sandbox_api).to receive(:get_sandbox).with('fork-1')
                                                 .and_return(build_sandbox_dto(id: 'fork-1', state: 'started'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      forked = started_sandbox.fork(timeout: 5)

      expect(forked.state).to eq('started')
      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
    end

    it 'stop: reads first when its subscription expired during the request' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'stopped'))
      allow(subscription_manager).to receive(:refresh).and_return(false)
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      started_sandbox.stop(5)

      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(started_sandbox.state).to eq('stopped')
    end

    it 'stop: reports the error reason that came with an error-state event, without a read' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      allow(started_sandbox).to receive(:poll_jitter_random).and_return(0.5)
      allow(ConditionVariable).to receive(:new).and_wrap_original do |original|
        original.call.tap do |cv|
          allow(cv).to receive(:wait).and_wrap_original do |wait, mutex, seconds|
            Thread.new do
              handler.last.call('sandbox.state.updated',
                                { 'sandbox' => { 'id' => 'sandbox-123', 'state' => 'error', 'errorReason' => 'oom' } })
            end
            wait.call(mutex, seconds)
          end
        end
      end

      expect do
        started_sandbox.stop(5)
      end.to raise_error(Daytona::Sdk::Error, /entered error state: error, error reason: oom/)
      expect(sandbox_api).not_to have_received(:get_sandbox)
    end

    it 'stop: reads the error reason once when the error-state event carries none' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopping'))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123')
                                                 .and_return(build_sandbox_dto(state: 'error',
                                                                               error_reason: 'disk full'))
      emit_when_waiting('error')

      expect { started_sandbox.stop(5) }
        .to raise_error(Daytona::Sdk::Error, /entered error state: error, error reason: disk full/)
      expect(sandbox_api).to have_received(:get_sandbox).once
    end

    it 'stop: releases the subscription and raises the original error when the request fails' do
      error = DaytonaApiClient::ApiError.new(code: 500, message: 'boom')
      allow(sandbox_api).to receive(:stop_sandbox).and_raise(error)

      expect { started_sandbox.stop(5) }.to raise_error(error)
      expect(subscription_manager).to have_received(:subscribe).once
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
      expect(sandbox_api).not_to have_received(:get_sandbox)
    end

    it 'stop: resolves at once and releases the subscription when the response is the target state' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'stopped'))

      started_sandbox.stop(5)

      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:subscribe).once
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'delete(wait: true): skips the first read when the subscription was live' do
      allow(sandbox_api).to receive(:delete_sandbox) do
        expect(subscription_manager).to have_received(:subscribe).once
        build_sandbox_dto(state: 'destroying')
      end
      emit_when_waiting('destroyed')

      started_sandbox.delete(5, wait: true)

      expect(started_sandbox.state).to eq('destroyed')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'pause: skips the first read when the subscription was live' do
      allow(sandbox_api).to receive(:pause_sandbox) do
        expect(subscription_manager).to have_received(:subscribe).once
        build_sandbox_dto(state: 'pausing')
      end
      emit_when_waiting('paused')

      started_sandbox.pause(timeout: 5)

      expect(started_sandbox.state).to eq('paused')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'resize: skips the first read when the subscription was live' do
      allow(sandbox_api).to receive(:resize_sandbox) do
        expect(subscription_manager).to have_received(:subscribe).once
        build_sandbox_dto(state: 'resizing')
      end
      emit_when_waiting('started')

      started_sandbox.resize(Daytona::Resources.new(cpu: 4), 5)

      expect(started_sandbox.state).to eq('started')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'stop: reads at once and fails at once when the response is an error state' do
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(state: 'error'))
      allow(sandbox_api).to receive(:get_sandbox).with('sandbox-123').and_return(build_sandbox_dto(state: 'error'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      expect { started_sandbox.stop(5) }.to raise_error(Daytona::Sdk::Error, /entered error state/)
      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    def stub_fork(state)
      allow(sandbox_api).to receive(:fork_sandbox).and_return(build_sandbox_dto(id: 'fork-1', state:))
    end

    it 'fork: skips the first read when the new sandbox subscription is live' do
      stub_fork('starting')
      emit_when_waiting('started')

      forked = started_sandbox.fork(timeout: 5)

      expect(forked.state).to eq('started')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).to have_received(:subscribe)
        .with(resource_id: 'fork-1', handler: kind_of(Method), events: ['sandbox.state.updated']).once
      expect(subscription_manager).to have_received(:unsubscribe).with('sub-123').once
    end

    it 'fork: reads once at once when the event connection is down' do
      allow(subscription_manager).to receive(:event_backed?).and_return(false)
      stub_fork('starting')
      allow(sandbox_api).to receive(:get_sandbox).with('fork-1')
                                                 .and_return(build_sandbox_dto(id: 'fork-1', state: 'started'))
      waits = []
      stub_condition_variable_waits { |seconds| waits << seconds }

      forked = started_sandbox.fork(timeout: 5)

      expect(forked.state).to eq('started')
      expect(sandbox_api).to have_received(:get_sandbox).once
      expect(waits).to be_empty
    end

    it 'fork: takes no wait and no subscription when the response is already started' do
      stub_fork('started')

      forked = started_sandbox.fork(timeout: 5)

      expect(forked.state).to eq('started')
      expect(sandbox_api).not_to have_received(:get_sandbox)
      expect(subscription_manager).not_to have_received(:subscribe)
    end

    it 'delete without wait takes no subscription' do
      allow(sandbox_api).to receive(:delete_sandbox).and_return(build_sandbox_dto(state: 'destroying'))

      started_sandbox.delete

      expect(subscription_manager).not_to have_received(:subscribe)
    end
  end

  describe 'a subscription rejected past the server limit' do
    let(:dispatcher) { Daytona::EventDispatcher.new(api_url: 'https://api.example.com/api', token: 'token') }
    let(:manager) { Daytona::EventSubscriptionManager.new(dispatcher) }
    let(:emit_lock) { Mutex.new }
    let(:emitted) { [] }
    let(:states) { Hash.new('pending').merge('sandbox-c' => 'stopped') }
    let(:cancelled) { Class.new(StandardError) }
    let(:waits) { [] }
    let(:limit_exceeded) { { 'ok' => false, 'error' => 'SUBSCRIPTION_LIMIT_EXCEEDED', 'message' => 'limit reached' } }

    before do
      allow(Daytona::SocketIOClient).to receive(:new) do
        double('SocketIOClient', connect: nil, close: nil).tap do |client|
          allow(client).to receive(:emit) { |name, data|
            emit_lock.synchronize {
              emitted << [name, data[:sandboxIds]]
            }
          }
          # The server is past its per-socket limit for sandbox-c only; it answers on its own thread.
          allow(client).to receive(:emit_with_ack) { |name, data, &on_ack|
            emit_lock.synchronize {
              emitted << [name, data[:sandboxIds]]
            }
            ack = data[:sandboxIds].include?('sandbox-c') ? limit_exceeded : { 'ok' => true }
            Thread.new { on_ack.call(ack) }
          }
        end
      end
      allow(sandbox_api).to receive(:get_sandbox) { |sandbox_id|
        build_sandbox_dto(id: sandbox_id, state: states[sandbox_id])
      }
    end

    after do
      waits.each do |thread|
        thread.raise(cancelled)
        thread.join
      end
      manager.shutdown
      dispatcher.disconnect
    end

    def sandbox_for(sandbox_id, state = 'pending')
      described_class.new(sandbox_dto: build_sandbox_dto(id: sandbox_id, state:), config:, sandbox_api:,
                          subscription_manager: manager)
    end

    # A stand-alone start wait on +sandbox_id+, running until the example ends or #cancel_wait.
    def start_wait(sandbox_id)
      sandbox = sandbox_for(sandbox_id)
      thread = Thread.new do
        sandbox.wait_for_sandbox_start(60)
      rescue cancelled
        nil
      end
      waits << thread
      thread
    end

    def emitted_events = emit_lock.synchronize { emitted.dup }

    def subscribed?(sandbox_id) = emitted_events.any? { |name, ids| name == 'subscribe' && ids.include?(sandbox_id) }

    def eventually(timeout: 5)
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
      while Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
        return if yield

        sleep(0.01)
      end
      raise RSpec::Expectations::ExpectationNotMetError, "condition not met within #{timeout} s"
    end

    def interval_for(sandbox)
      allow(sandbox).to receive(:poll_jitter_random).and_return(0.5)
      sandbox.send(:next_poll_interval, subscription: { id: 'held' }, fast_poll_interval: 0.1)
    end

    it 'makes the rejected wait read at once and fall back; the served ones stay event-backed' do
      start_wait('sandbox-a')
      start_wait('sandbox-b')
      eventually { subscribed?('sandbox-a') && subscribed?('sandbox-b') && manager.connected? }
      sandbox_c = sandbox_for('sandbox-c', 'started')
      allow(sandbox_c).to receive(:poll_jitter_random).and_return(0.5)
      allow(sandbox_api).to receive(:stop_sandbox).and_return(build_sandbox_dto(id: 'sandbox-c', state: 'stopping'))

      elapsed = Benchmark.realtime { sandbox_c.stop(5) }

      expect(sandbox_c.state).to eq('stopped')
      expect(sandbox_api).to have_received(:get_sandbox).with('sandbox-c').once
      expect(elapsed).to be < 2.5
      expect(subscribed?('sandbox-c')).to be(true)
      expect(manager.event_backed?('sandbox-a')).to be(true)
      expect(interval_for(sandbox_for('sandbox-a'))).to eq(5.0)
    end
  end

  describe 'interrupts while the wait takes or releases its subscription' do
    let(:dispatcher) { Daytona::EventDispatcher.new(api_url: 'https://api.example.com/api', token: 'token') }
    let(:manager) { Daytona::EventSubscriptionManager.new(dispatcher) }
    let(:pending_sandbox) do
      described_class.new(
        sandbox_dto: build_sandbox_dto(state: 'pending'),
        config: config,
        sandbox_api: sandbox_api,
        subscription_manager: manager
      )
    end
    let(:interrupt) { Class.new(StandardError) }

    before do
      allow(Daytona::SocketIOClient).to receive(:new) do
        double('SocketIOClient', connect: nil, emit: true, emit_with_ack: true, close: nil)
      end
      stub_condition_variable_waits { |_seconds| nil }
    end

    after do
      manager.shutdown
      dispatcher.disconnect
    end

    # As Timeout.timeout does: raises into the waiting thread from another thread.
    def interrupt_current_thread
      waiting = Thread.current
      Thread.new { waiting.raise(interrupt, 'interrupted') }.join
    end

    def listener_ids
      dispatcher.instance_variable_get(:@listeners).keys
    end

    it 'still releases the subscription when interrupted while releasing it' do
      allow(dispatcher).to receive(:subscribe).and_wrap_original do |original, *args, **kwargs, &handler|
        release = original.call(*args, **kwargs, &handler)
        lambda do
          interrupt_current_thread
          release.call
        end
      end
      allow(sandbox_api).to receive(:get_sandbox).and_return(build_sandbox_dto(state: 'started'))

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(interrupt, 'interrupted')
      expect(listener_ids).not_to include('sandbox-123')
    end

    it 'releases the subscription when interrupted while taking it' do
      allow(dispatcher).to receive(:subscribe).and_wrap_original do |original, *args, **kwargs, &handler|
        original.call(*args, **kwargs, &handler).tap { interrupt_current_thread }
      end
      allow(sandbox_api).to receive(:get_sandbox).and_return(build_sandbox_dto(state: 'started'))

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(interrupt, 'interrupted')
      expect(listener_ids).not_to include('sandbox-123')
    end

    it 'releases the new subscription when interrupted while subscribing again after expiry' do
      subscribes = 0
      allow(dispatcher).to receive(:subscribe).and_wrap_original do |original, *args, **kwargs, &handler|
        subscribes += 1
        original.call(*args, **kwargs, &handler).tap { interrupt_current_thread if subscribes == 2 }
      end
      # The first subscription expires before the first renewal.
      allow(manager).to receive(:refresh) do |sub_id|
        manager.unsubscribe(sub_id)
        false
      end
      allow(sandbox_api).to receive(:get_sandbox).and_return(build_sandbox_dto(state: 'pending'))

      expect { pending_sandbox.wait_for_sandbox_start }.to raise_error(interrupt, 'interrupted')
      expect(subscribes).to eq(2)
      expect(listener_ids).not_to include('sandbox-123')
    end
  end

  describe '#with_timeout (private)' do
    it 'raises TimeoutError promptly when setup exhausts a positive timeout budget' do
      elapsed = Benchmark.realtime do
        expect do
          sandbox.send(:with_timeout, message: 'timed out', timeout: 0.01, setup: proc { sleep 0.02 }) { sleep 999 }
        end.to raise_error(Daytona::Sdk::TimeoutError, 'timed out')
      end

      expect(elapsed).to be < 2
    end

    it 'does not time out when timeout is zero (no-timeout semantics)' do
      result = sandbox.send(:with_timeout, message: 'timed out', timeout: 0, setup: nil) { :done }

      expect(result).to eq(:done)
    end
  end
end
