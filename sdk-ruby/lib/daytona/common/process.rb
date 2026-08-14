# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'base64'

module Daytona
  class ProcessRunFrameDecoder
    def initialize
      @buffers = { stdout: ''.b, stderr: ''.b }
    end

    def decode(channel:, data:, encoding:)
      output_channel = case channel
                       when 'stderr' then :stderr
                       when 'stdout', 'pty' then :stdout
                       end
      return [nil, ''] unless output_channel
      return [output_channel, data.to_s] unless encoding == 'base64'

      @buffers[output_channel] << Base64.strict_decode64(data.to_s)
      [output_channel, extract_valid_prefix(output_channel)]
    end

    def flush
      %i[stdout stderr].map do |channel|
        bytes = @buffers[channel]
        @buffers[channel] = ''.b
        bytes.force_encoding(Encoding::UTF_8).encode(
          Encoding::UTF_8, invalid: :replace, undef: :replace, replace: '�'
        )
      end
    end

    private

    def extract_valid_prefix(channel)
      bytes = @buffers[channel]
      0.upto([3, bytes.bytesize].min) do |tail_size|
        prefix_size = bytes.bytesize - tail_size
        prefix = bytes.byteslice(0, prefix_size).dup.force_encoding(Encoding::UTF_8)
        next unless prefix.valid_encoding?

        @buffers[channel] = tail_size.zero? ? ''.b : bytes.byteslice(prefix_size, tail_size)
        return prefix
      end
      ''
    end
  end

  class ProcessOutput
    # @return [String] Collected standard output
    attr_reader :stdout

    # @return [String] Collected standard error
    attr_reader :stderr

    # @return [Integer, nil] Terminal exit code, if available
    attr_reader :exit_code

    # @return [String, nil] Terminal signal, if available
    attr_reader :signal

    # @return [String, nil] Terminal reason reported by the process service
    attr_reader :reason

    # Initialize collected process output.
    #
    # @param stdout [String] Collected standard output
    # @param stderr [String] Collected standard error
    # @param exit_code [Integer, nil] Terminal exit code
    # @param signal [String, nil] Terminal signal
    # @param reason [String, nil] Terminal reason
    def initialize(stdout:, stderr:, exit_code: nil, signal: nil, reason: nil)
      @stdout = stdout
      @stderr = stderr
      @exit_code = exit_code
      @signal = signal
      @reason = reason
    end
  end

  class ProcessRunResult < ProcessOutput
    # @return [String] Process ID
    attr_reader :id

    # @return [Daytona::ProcessHandle] Handle for later supervision or cleanup
    attr_reader :handle

    # @return [Boolean] Whether the wait ended because its timeout elapsed
    attr_reader :timed_out

    # Initialize a one-shot process result.
    #
    # @param id [String] Process ID
    # @param handle [Daytona::ProcessHandle] Supervising process handle
    # @param timed_out [Boolean] Whether the wait timed out
    # @param output [Hash] Arguments accepted by {ProcessOutput#initialize}
    def initialize(id:, handle:, timed_out: false, **output)
      super(**output)
      @id = id
      @handle = handle
      @timed_out = timed_out
    end
  end

  class ExecuteResponse
    # @return [Integer] The exit code from the command execution
    attr_reader :exit_code

    # @return [String] The output from the command execution
    attr_reader :result

    # @return [ExecutionArtifacts, nil] Artifacts from the command execution
    attr_reader :artifacts

    # @return [Hash] Additional properties from the response
    attr_reader :additional_properties

    # Initialize a new ExecuteResponse
    #
    # @param exit_code [Integer] The exit code from the command execution
    # @param result [String] The output from the command execution
    # @param artifacts [ExecutionArtifacts, nil] Artifacts from the command execution
    # @param additional_properties [Hash] Additional properties from the response
    def initialize(exit_code:, result:, artifacts: nil, additional_properties: {})
      @exit_code = exit_code
      @result = result
      @artifacts = artifacts
      @additional_properties = additional_properties
    end
  end

  class ExecutionArtifacts
    # @return [String] Standard output from the command, same as `result` in `ExecuteResponse`
    attr_accessor :stdout

    # @return [Array] List of chart metadata from matplotlib
    attr_accessor :charts

    # Initialize a new ExecutionArtifacts
    #
    # @param stdout [String] Standard output from the command
    # @param charts [Array] List of chart metadata from matplotlib
    def initialize(stdout = '', charts = [])
      @stdout = stdout
      @charts = charts
    end
  end

  class CodeRunParams
    # @return [Array<String>, nil] Command line arguments
    attr_accessor :argv

    # @return [Hash<String, String>, nil] Environment variables
    attr_accessor :env

    # Initialize a new CodeRunParams
    #
    # @param argv [Array<String>, nil] Command line arguments
    # @param env [Hash<String, String>, nil] Environment variables
    def initialize(argv: nil, env: nil)
      @argv = argv
      @env = env
    end
  end

  class SessionExecuteRequest
    # @return [String] The command to execute
    attr_accessor :command

    # @return [Boolean] Whether to execute the command asynchronously
    attr_accessor :run_async

    # @return [Boolean] Whether to suppress input echo
    attr_accessor :suppress_input_echo

    # Initialize a new SessionExecuteRequest
    #
    # @param command [String] The command to execute
    # @param run_async [Boolean] Whether to execute the command asynchronously
    # @param suppress_input_echo [Boolean] Whether to suppress input echo (default is false)
    def initialize(command:, run_async: false, suppress_input_echo: false)
      @command = command
      @run_async = run_async
      @suppress_input_echo = suppress_input_echo
    end
  end

  class SessionExecuteResponse
    # @return [String, nil] Unique identifier for the executed command
    attr_reader :cmd_id

    # @return [String, nil] The output from the command execution
    attr_reader :output

    # @return [String, nil] Standard output from the command
    attr_reader :stdout

    # @return [String, nil] Standard error from the command
    attr_reader :stderr

    # @return [Integer, nil] The exit code from the command execution
    attr_reader :exit_code

    # @return [Hash] Additional properties from the response
    attr_reader :additional_properties

    # Initialize a new SessionExecuteResponse
    #
    # @param opts [Hash] Options for the SessionExecuteResponse
    # @param cmd_id [String, nil] Unique identifier for the executed command
    # @param output [String, nil] The output from the command execution
    # @param stdout [String, nil] Standard output from the command
    # @param stderr [String, nil] Standard error from the command
    # @param exit_code [Integer, nil] The exit code from the command execution
    # @param additional_properties [Hash] Additional properties from the response
    def initialize(opts = {})
      @cmd_id = opts.fetch(:cmd_id, nil)
      @output = opts.fetch(:output, nil)
      @stdout = opts.fetch(:stdout, nil)
      @stderr = opts.fetch(:stderr, nil)
      @exit_code = opts.fetch(:exit_code, nil)
      @additional_properties = opts.fetch(:additional_properties, {})
    end
  end

  class SessionCommandLogsResponse
    # @return [String, nil] The combined output from the command
    attr_reader :output

    # @return [String, nil] The stdout from the command
    attr_reader :stdout

    # @return [String, nil] The stderr from the command
    attr_reader :stderr

    # Initialize a new SessionCommandLogsResponse
    #
    # @param output [String, nil] The combined output from the command
    # @param stdout [String, nil] The stdout from the command
    # @param stderr [String, nil] The stderr from the command
    def initialize(output: nil, stdout: nil, stderr: nil)
      @output = output
      @stdout = stdout
      @stderr = stderr
    end
  end
end
