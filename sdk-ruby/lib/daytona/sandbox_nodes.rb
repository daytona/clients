# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'securerandom'
require 'timeout'

module Daytona
  # Resource allocations for one additional whole-GPU node.
  class NodeResources
    attr_reader :cpu
    attr_reader :memory
    attr_reader :disk

    def initialize(cpu:, memory:, disk:)
      unless [cpu, memory, disk].all? { |v| v.is_a?(Integer) && v.positive? }
        raise Sdk::Error, 'Node CPU, memory and disk must be positive integers'
      end

      @cpu = cpu
      @memory = memory
      @disk = disk
    end

    def to_h = { cpu:, memory:, disk: }
  end

  module Sdk
    # All ranks are allowed to settle; successful results remain available.
    class NodeOperationError < Error
      attr_reader :failures
      attr_reader :results

      def initialize(failures, results)
        @failures = failures.sort.to_h.freeze
        @results = results.sort.to_h.freeze
        super("Operation failed on node ranks: #{@failures.keys.join(', ')}")
      end
    end
  end

  # One budget for refresh, submission, polling and final refresh. A copied
  # generated client avoids changing timeouts on clients shared by other calls.
  class NodeDeadline
    def initialize(seconds)
      unless seconds.is_a?(Numeric) && seconds.finite? && seconds >= 0
        raise Sdk::Error, 'Timeout must be a finite non-negative number of seconds'
      end

      @ends_at = now + seconds unless seconds.zero?
    end

    def remaining
      return 0 unless @ends_at

      value = @ends_at - now
      raise Sdk::TimeoutError, 'Sandbox node operation exceeded its timeout' unless value.positive?

      value
    end

    def call(api, method, *)
      budget = remaining
      client = api.api_client.dup
      client.config = api.api_client.config.dup
      configured = client.config.timeout
      client.config.timeout = if budget.zero?
                                configured
                              elsif configured && configured.positive?
                                [configured, budget].min
                              else
                                budget
                              end
      result = api.class.new(client).public_send(method, *)
      remaining
      result
    rescue *Sdk::API_ERROR_CLASSES => e
      remaining
      raise Sdk.wrap_error(e, 'Sandbox node operation failed')
    end

    def pause(seconds)
      budget = remaining
      sleep(budget.zero? ? seconds : [seconds, budget].min)
      remaining
    end

    private

    def now = ::Process.clock_gettime(::Process::CLOCK_MONOTONIC)
  end
  private_constant :NodeDeadline

  # A stable rank/id handle with its own toolbox route.
  class SandboxNode
    attr_reader :id
    attr_reader :rank
    attr_reader :process
    attr_reader :fs
    attr_reader :process_api

    def initialize(parent, dto)
      @parent = parent
      @id = dto.id
      @rank = dto.rank
      @warnings = []
      @warning_mutex = Mutex.new
      @toolbox_config = parent.send(:build_toolbox_api_config).dup
      client = DaytonaToolboxApiClient::ApiClient.new(@toolbox_config)
      client.default_headers['Authorization'] = "Bearer #{parent.config.api_key || parent.config.jwt_token}"
      client.default_headers['X-Daytona-Source'] = 'sdk-ruby'
      client.default_headers['X-Daytona-SDK-Version'] = Sdk::VERSION
      client.default_headers['X-Daytona-Organization-ID'] = parent.config.organization_id if parent.config.jwt_token
      client.user_agent = "sdk-ruby/#{Sdk::VERSION}"
      @process_api = DaytonaToolboxApiClient::ProcessApi.new(client)
      @process = Process.new(sandbox_id: id, toolbox_api: process_api,
                             get_preview_link: method(:preview_url),
                             language: (parent.labels || {}).fetch(CODE_TOOLBOX_LANGUAGE_LABEL, 'python'),
                             otel_state: parent.send(:otel_state))
      @fs = FileSystem.new(sandbox_id: id, toolbox_api: DaytonaToolboxApiClient::FileSystemApi.new(client),
                           otel_state: parent.send(:otel_state))
      update(dto)
    end

    def update(dto)
      raise Sdk::Error, 'Sandbox node identity changed during refresh' unless dto.id == id && dto.rank == rank

      update_proxy
      @data = dto
    end

    def update_proxy
      encoded = URI.encode_www_form_component(id).gsub('+', '%20')
      url = URI.parse("#{@parent.toolbox_proxy_url.sub(%r{/+$}, '')}/#{encoded}")
      @toolbox_config.scheme = url.scheme
      @toolbox_config.host = url.host + (url.port == url.default_port ? '' : ":#{url.port}")
      @toolbox_config.base_path = url.path
    end

    # @return [Integer]
    def cpu = @data.cpu

    # @return [Integer]
    def gpu = @data.gpu

    # @return [Integer]
    def memory = @data.memory

    # @return [Integer]
    def disk = @data.disk

    # @return [String]
    def state = @data.state

    # @return [Integer]
    def incarnation = @data.incarnation

    # @return [DaytonaApiClient::GangNodeFailure, nil]
    def failure = @data.failure

    # @return [String, nil]
    def fabric_address = @data.fabric_address

    # @return [Integer, nil]
    def fabric_prefix_length = @data.fabric_prefix_length

    # @return [Boolean, nil]
    def fabric_healthy = @data.fabric_healthy

    # @return [String, nil]
    def fabric_unhealthy_since = @data.fabric_unhealthy_since

    # @return [Array<String>] Advisory preflight and cleanup warnings
    def warnings = @warning_mutex.synchronize { @warnings.dup.freeze }

    def warn(message)
      @warning_mutex.synchronize { @warnings << message unless @warnings.include?(message) }
    end

    private :update, :update_proxy, :warn, :process_api

    # @return [DaytonaApiClient::PortPreviewUrl] Preview for this member
    def preview_url(port) = api_call(:get_port_preview_url, id, port)
    # @return [DaytonaApiClient::SshAccessDto] SSH access for this member
    def create_ssh_access(expires_in_minutes = nil) = api_call(:create_ssh_access, id, { expires_in_minutes: })
    # @return [DaytonaApiClient::Sandbox] Updated sandbox metadata
    def revoke_ssh_access(token) = api_call(:revoke_ssh_access, id, { token: })

    private

    def api_call(method, *)
      @parent.sandbox_api.public_send(method, *)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Sandbox node request failed')
    end
  end

  # Rank-ordered nodes, including rank zero. Membership helpers wait by default;
  # timing out ends the SDK wait and does not cancel a submitted operation.
  class SandboxNodes
    include Enumerable

    GPUS_PER_NODE = 8
    attr_reader :process
    attr_reader :fs

    def initialize(parent)
      @parent = parent
      @members = [].freeze
      @process = NodeProcess.new(self)
      @fs = NodeFileSystem.new(self)
    end

    # @yieldparam node [Daytona::SandboxNode]
    # @return [Enumerator, Array<Daytona::SandboxNode>]
    def each(&) = @members.each(&)
    # @param rank [Integer] Logical rank; zero addresses the head
    # @return [Daytona::SandboxNode, nil]
    def [](rank) = @members[rank]
    # @return [Integer] Current membership size
    def size = @members.size
    alias length size
    # @return [Array<Daytona::SandboxNode>] Snapshot of current membership
    def to_a = @members.dup

    def update(raw)
      if raw.nil? || raw.empty?
        unless @members.empty?
          @members.each { |node| node.send(:update_proxy) }
          return
        end
        count = [@parent.node_count, 1].max
        raw = [DaytonaApiClient::SandboxNode.new(id: @parent.id, rank: 0, incarnation: 1, state: @parent.state,
                                                 cpu: @parent.cpu.to_i / count, gpu: @parent.gpu.to_i / count,
                                                 memory: @parent.memory.to_i / count, disk: @parent.disk.to_i / count)]
      end
      existing = @members.to_h { |node| [node.id, node] }
      unless raw.all? { |dto| dto.is_a?(DaytonaApiClient::SandboxNode) }
        raise Sdk::Error, 'Invalid sandbox node metadata'
      end

      ids = raw.map(&:id)
      valid = ids.uniq.size == ids.size && raw.each_with_index.all? do |dto, rank|
        dto.rank == rank && dto.id.is_a?(String) && !['', '.', '..'].include?(dto.id) &&
          (rank != 0 || dto.id == @parent.id) && (!existing[dto.id] || existing[dto.id].rank == rank)
      end
      raise Sdk::Error, 'Invalid sandbox node ordering or identity' unless valid

      @members = raw.map do |dto|
        existing[dto.id]&.tap { |node| node.send(:update, dto) } || SandboxNode.new(@parent, dto)
      end.freeze
    end

    # @param nodes [Array<Daytona::NodeResources>] Resources in appended rank order
    # @param timeout [Numeric] Overall wait budget in seconds; zero disables it
    # @return [DaytonaApiClient::GangMembershipOperation]
    def add(nodes:, timeout: 600)
      unless nodes.is_a?(Array) && !nodes.empty? && nodes.all? { |node| node.is_a?(NodeResources) }
        raise Sdk::Error, 'Provide a non-empty array of NodeResources'
      end

      deadline = NodeDeadline.new(timeout)
      body = DaytonaApiClient::AddGangNodes.new(expected_generation: generation(deadline), nodes: nodes.map do |node|
        DaytonaApiClient::GangNodeResources.new(node.to_h)
      end)
      submit(:add_sandbox_nodes, body, deadline)
    end

    # @param count [Integer] Number of highest ranks to release
    # @param timeout [Numeric] Overall wait budget in seconds
    # @return [DaytonaApiClient::GangMembershipOperation]
    def release(count: 1, timeout: 600)
      raise Sdk::Error, 'Node release count must be a positive integer' unless count.is_a?(Integer) && count.positive?

      deadline = NodeDeadline.new(timeout)
      body = DaytonaApiClient::RemoveGangNodes.new(count:, expected_generation: generation(deadline))
      submit(:remove_sandbox_nodes, body, deadline)
    end

    # @param rank [Integer] Failed logical rank to replace
    # @param timeout [Numeric] Overall wait budget in seconds
    # @return [DaytonaApiClient::GangMembershipOperation]
    def replace(rank:, timeout: 600)
      raise Sdk::Error, 'Node rank must be a non-negative integer' unless rank.is_a?(Integer) && rank >= 0

      deadline = NodeDeadline.new(timeout)
      body = DaytonaApiClient::ReplaceGangNode.new(expected_generation: generation(deadline))
      submit(:replace_sandbox_node, body, deadline, rank.to_s)
    end

    # Capture membership before starting threads. On interruption, allow each
    # task's ensure block to remove its private session before returning.
    def parallel
      threads = @members.map do |node|
        [node.rank, Thread.new do
          [yield(node), nil]
        rescue StandardError => e
          [nil, e]
        end]
      end
      results = {}
      failures = {}
      threads.each do |rank, thread|
        result, error = thread.value
        error ? failures[rank] = error : results[rank] = result
      end
      raise Sdk::NodeOperationError.new(failures, results) unless failures.empty?

      results.sort.to_h
    ensure
      threads&.each { |_, thread| thread.kill if thread.alive? }
      threads&.each { |_, thread| thread.join }
    end

    private :update, :parallel

    private

    def generation(deadline)
      refresh(deadline)
      @parent.topology_generation || (raise Sdk::Error, 'Sandbox does not expose a topology generation')
    end

    def refresh(deadline)
      dto = deadline.call(@parent.sandbox_api, :get_sandbox, @parent.id)
      @parent.send(:process_response, dto)
    end

    def submit(method, body, deadline, rank = nil)
      args = [@parent.id, rank, body].compact
      op = deadline.call(@parent.sandbox_api, method, *args)
      until %w[COMPLETED FAILED].include?(op.status)
        deadline.pause(1)
        op = deadline.call(@parent.sandbox_api, :get_gang_membership_operation, @parent.id, op.id)
      end
      raise Sdk::Error, op.error_reason || 'Node membership operation failed' if op.status == 'FAILED'

      refresh(deadline)
      op
    end
  end

  class NodeProcess
    RDMA_CHECK = 'sh -lc \'ldconfig -p 2>/dev/null | grep -q libibverbs.so.1 && find /usr/lib /lib -name "libmlx5-rdmav*.so" -print -quit 2>/dev/null | grep -q .\''
    RDMA_WARNING = 'WARNING: rdma-core userspace (libibverbs.so.1 and an mlx5 provider) is missing; NCCL may fall back to sockets.'

    def initialize(nodes) = @nodes = nodes

    # Callbacks receive (rank, chunk). A positive timeout bounds each command;
    # private session cleanup has its own ten-second budget after expiration.
    # @param command [String] Shell command to execute on each current member
    # @param timeout [Numeric] Execution limit in seconds per rank
    # @param on_stdout [Proc, nil] Callback receiving (rank, chunk)
    # @param on_stderr [Proc, nil] Callback receiving (rank, chunk)
    # @return [Hash<Integer, Daytona::ExecuteResponse>] Results keyed by rank
    # @raise [Daytona::Sdk::NodeOperationError] Every failed rank and successful result
    def exec(command:, timeout: 0, on_stdout: nil, on_stderr: nil)
      NodeDeadline.new(timeout)
      lock = Mutex.new
      active = true
      stdout = ->(rank, chunk) { lock.synchronize { on_stdout&.call(rank, chunk) if active } }
      stderr = ->(rank, chunk) { lock.synchronize { on_stderr&.call(rank, chunk) if active } }
      check_rdma = @nodes.size > 1
      @nodes.send(:parallel) do |node|
        NodeExecution.new(
          node,
          command,
          timeout:,
          callbacks: { stdout: on_stdout && stdout, stderr: on_stderr && stderr },
          check_rdma:
        ).run
      end
    ensure
      lock&.synchronize { active = false }
    end
  end

  # One command's private session; state is never shared across concurrent calls.
  class NodeExecution
    RDMA_CHECK = NodeProcess::RDMA_CHECK
    RDMA_WARNING = NodeProcess::RDMA_WARNING

    def initialize(node, command, timeout:, callbacks:, check_rdma:)
      @node = node
      @api = node.send(:process_api)
      @command = command
      @timeout = timeout
      @callbacks = callbacks
      @check_rdma = check_rdma
      @session = "daytona-nodes-exec-#{SecureRandom.uuid}"
    end

    def run
      @warning = @check_rdma ? rdma_warning : ''
      emit_warning
      begin
        NodeDeadline.new(10).call(@api, :create_session,
                                  DaytonaToolboxApiClient::CreateSessionRequest.new(session_id: @session))
        execute_command
      rescue StandardError => e
        failure = e
        raise
      ensure
        cleanup(failure)
      end
    end

    private

    def rdma_warning
      request = DaytonaToolboxApiClient::ExecuteRequest.new(command: RDMA_CHECK, timeout: 5)
      response = NodeDeadline.new(5).call(@api, :execute_command, request)
      response.exit_code.zero? ? '' : RDMA_WARNING
    rescue StandardError
      'WARNING: Could not check RDMA userspace; continuing without verification.'
    end

    def emit_warning
      return if @warning.empty?

      @node.send(:warn, @warning)
      @callbacks[:stderr]&.call(@node.rank, "#{@warning}\n")
    end

    def execute_command
      deadline = NodeDeadline.new(@timeout)
      req = DaytonaToolboxApiClient::SessionExecuteRequest.new(command: @command, run_async: true)
      command_id = deadline.call(@api, :session_execute_command, @session, req).cmd_id
      raise Sdk::Error, 'Session execution returned no command ID' if command_id.nil? || command_id.empty?

      stream(command_id, deadline) if @callbacks.values.any?
      completed = wait_for_command(command_id, deadline)
      logs = deadline.call(@api, :get_session_command_logs, @session, command_id)
      result(completed, logs)
    end

    def stream(command_id, deadline)
      NodeLogStream.new(@node, @session, command_id, deadline, @callbacks).run
    end

    def wait_for_command(command_id, deadline)
      loop do
        completed = deadline.call(@api, :get_session_command, @session, command_id)
        return completed unless completed.exit_code.nil?

        deadline.pause(0.1)
      end
    end

    def result(completed, logs)
      prefix = @warning.empty? ? '' : "#{@warning}\n"
      ExecuteResponse.new(exit_code: completed.exit_code,
                          result: prefix + (logs.output || "#{logs.stdout}#{logs.stderr}"),
                          artifacts: ExecutionArtifacts.new(prefix + (logs.stdout || '')))
    end

    def cleanup(failure)
      NodeDeadline.new(10).call(@api, :delete_session, @session)
    rescue StandardError => e
      prefix = failure ? 'Session cleanup also failed' : "Session #{@session} cleanup failed"
      @node.send(:warn, "#{prefix}: #{e.message}")
    end
  end
  private_constant :NodeExecution

  # Owns the stream socket so timeouts cannot leave an idle reader behind.
  class NodeLogStream
    def initialize(node, session, command, deadline, callbacks)
      @node = node
      @deadline = deadline
      @callbacks = callbacks
      client = node.send(:process_api).api_client
      @headers = client.default_headers.dup
      @url = URI.parse(client.config.base_url)
      @url.scheme = @url.scheme == 'https' ? 'wss' : 'ws'
      @url.path = "#{@url.path.sub(%r{/+$}, '')}/process/session/#{session}/command/#{command}/logs"
      @url.query = 'follow=true'
      @completion = Queue.new
      @lock = Mutex.new
      @active = true
    end

    def run
      timeout = @deadline.remaining
      if timeout.zero?
        connect_and_wait
      else
        Timeout.timeout(timeout, Sdk::TimeoutError, 'Session log stream exceeded its timeout') { connect_and_wait }
      end
    ensure
      @lock.synchronize { @active = false }
      close_socket
    end

    private

    def connect_and_wait
      Common::WebSocketDialer.connect(@url.to_s, headers: @headers) do |socket|
        @socket = socket
        socket.on(:message) { |message| on_message(message) }
        socket.on(:close) { @completion.push(nil) }
        socket.on(:error) { |error| @completion.push(Sdk::Error.new("WebSocket error: #{error.message}")) }
      end
      failure = @completion.pop
      raise failure if failure
    end

    def on_message(message)
      return @completion.push(nil) if message.type == :close

      stdout, stderr = Util.demux(message.data.to_s)
      @lock.synchronize do
        return unless @active

        @deadline.remaining
        @callbacks[:stdout]&.call(@node.rank, stdout) unless stdout.empty?
        @callbacks[:stderr]&.call(@node.rank, stderr) unless stderr.empty?
      end
    rescue StandardError => e
      @completion.push(e)
    end

    def close_socket
      @socket&.close
    rescue StandardError => e
      @node.send(:warn, "Log stream cleanup failed: #{e.message}")
    end
  end
  private_constant :NodeLogStream

  class NodeFileSystem
    def initialize(nodes) = @nodes = nodes

    # A shared IO must be read once before parallel uploads. Individual uploads
    # own their file descriptors; successful uploads are not rolled back.
    def upload_file(source, remote_path)
      source = source.read if source.respond_to?(:read)
      @nodes.send(:parallel) { |node| node.fs.upload_file(source, remote_path) }
      nil
    end
  end
end
