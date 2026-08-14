# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'base64'

module Daytona
  # Incremental UTF-8 decoder for process log frames.
  #
  # The process service ships raw byte chunks (base64) that can split a multibyte
  # UTF-8 sequence across frames, so each output channel decodes as a continuous
  # stream or split codepoints corrupt into U+FFFD. Undecodable bytes become
  # U+FFFD immediately; only a trailing sequence that is still completable is held
  # back for the next frame. {#flush} drains any dangling bytes at EOF.
  class ProcessRunFrameDecoder
    REPLACEMENT_CHARACTER = "\u{FFFD}"
    private_constant :REPLACEMENT_CHARACTER

    UTF8_CONTINUATION_BYTES = (0x80..0xBF)
    private_constant :UTF8_CONTINUATION_BYTES

    # Longest trailing byte count that can still be an incomplete sequence: a
    # 4-byte sequence missing only its last byte.
    MAX_PENDING_BYTES = 3
    private_constant :MAX_PENDING_BYTES

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
        decode_with_replacement(bytes)
      end
    end

    private

    def extract_valid_prefix(channel)
      bytes = @buffers[channel]
      pending_size = pending_suffix_size(bytes)
      prefix_size = bytes.bytesize - pending_size
      @buffers[channel] = pending_size.zero? ? ''.b : bytes.byteslice(prefix_size, pending_size)
      return '' if prefix_size.zero?

      decode_with_replacement(bytes.byteslice(0, prefix_size))
    end

    # Trailing byte count that starts a multibyte sequence still missing bytes.
    # Anything else - ASCII, a complete sequence, or a byte that can never lead
    # one - is decodable now, so nothing is withheld from the caller.
    def pending_suffix_size(bytes)
      size = bytes.bytesize
      1.upto([MAX_PENDING_BYTES, size].min) do |tail_size|
        byte = bytes.getbyte(size - tail_size)
        sequence_size = utf8_sequence_size(byte)
        if sequence_size
          return 0 unless sequence_size > tail_size

          return completable_suffix?(bytes, size - tail_size, tail_size) ? tail_size : 0
        end
        return 0 unless UTF8_CONTINUATION_BYTES.cover?(byte)
      end
      0
    end

    # A retained suffix must still be completable into a valid character:
    # overlong and out-of-range prefixes (E0 80, F0 8x, F4 9x) can never
    # decode, so they are replaced immediately instead of being buffered
    # until EOF. ED (surrogate range) is deliberately NOT boundary-checked -
    # Python's incremental codec retains ED A0 until the next chunk, and this
    # decoder pins byte-identical parity with it.
    def completable_suffix?(bytes, lead_index, tail_size)
      (1...tail_size).all? do |offset|
        range = offset == 1 ? first_continuation_range(bytes.getbyte(lead_index)) : UTF8_CONTINUATION_BYTES
        range.cover?(bytes.getbyte(lead_index + offset))
      end
    end

    def first_continuation_range(lead)
      case lead
      when 0xE0 then (0xA0..0xBF)
      when 0xF0 then (0x90..0xBF)
      when 0xF4 then (0x80..0x8F)
      else UTF8_CONTINUATION_BYTES
      end
    end

    def utf8_sequence_size(byte)
      return 2 if (0xC2..0xDF).cover?(byte)
      return 3 if (0xE0..0xEF).cover?(byte)
      return 4 if (0xF0..0xF4).cover?(byte)

      nil
    end

    def decode_with_replacement(bytes)
      bytes.dup.force_encoding(Encoding::UTF_8).scrub(REPLACEMENT_CHARACTER)
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
