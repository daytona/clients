# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'cgi'
require 'json'
require 'net/http'
require 'timeout'
require 'uri'

module Daytona
  class ProcessHandle # rubocop:disable Metrics/ClassLength
    # Raised by the hand-rolled log stream and terminal socket rather than the
    # generated client, so they are mapped to {Sdk::ConnectionError} by hand.
    TRANSPORT_ERROR_CLASSES = [IOError, SystemCallError, Timeout::Error].freeze
    private_constant :TRANSPORT_ERROR_CLASSES

    # @return [String] Process ID used by {Process#get} and {Process#connect}
    attr_reader :id

    # Initialize a process handle.
    #
    # @param process_id [String] Process ID
    # @param toolbox_api [DaytonaToolboxApiClient::ProcessApi] Toolbox process client
    def initialize(process_id:, toolbox_api:)
      @id = process_id
      @toolbox_api = toolbox_api
    end

    # Retrieve the current process record, including state and terminal metadata.
    #
    # @return [DaytonaToolboxApiClient::Process] Current process record
    # @raise [Daytona::Sdk::Error] If the process cannot be retrieved
    def get
      toolbox_api.get_process(id)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to get process')
    end

    # Replay a page of retained process logs.
    #
    # Continue paging with the returned `next_cursor` until `eof` is true. If the
    # service reports `CURSOR_EXPIRED`, restart from the reported first available
    # cursor. Logs can only be replayed while retained by the process policy.
    #
    # @param cursor [String, nil] Cursor to resume from, or nil for the available start
    # @param limit [Integer, nil] Maximum number of frames to return
    # @param encoding [String] Frame encoding, `text` or `base64`
    # @return [DaytonaToolboxApiClient::ProcessLogPage] Frames and the next cursor
    # @raise [Daytona::Sdk::Error] If logs cannot be read, including an expired cursor
    def logs(cursor: nil, limit: nil, encoding: 'text')
      toolbox_api.read_process_logs(id, **{ cursor:, limit:, encoding: }.compact)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to get process logs')
    end

    # Replay retained logs from a cursor, then follow live events until EOF.
    #
    # Yields log, state, warning, and EOF event hashes. A `CURSOR_EXPIRED` warning
    # includes `first_available_cursor`; reconnect with that cursor to recover from
    # the oldest still-retained frame. Without a block, returns an Enumerator.
    #
    # @param cursor [String, nil] Cursor to resume replay from
    # @param encoding [String] Log frame encoding, `text` or `base64`
    # @yield [Hash] Replay-then-live stream event
    # @return [Enumerator, nil] Enumerator without a block, otherwise nil after EOF
    # @raise [Daytona::Sdk::ConnectionError] If the stream cannot be read or parsed
    def stream_logs(cursor: nil, encoding: 'text', &)
      return enum_for(__method__, cursor:, encoding:) unless block_given?

      stream_sse(cursor:, encoding:, &)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to stream process logs')
    rescue JSON::ParserError, *TRANSPORT_ERROR_CLASSES => e
      raise Sdk::ConnectionError, "Failed to stream process logs: #{e.message}"
    end

    # Send input to a running process.
    #
    # @param data [#to_s] Input data
    # @return [nil]
    # @raise [Daytona::Sdk::Error] If input cannot be sent
    def stdin(data)
      toolbox_api.send_process_stdin(id, DaytonaToolboxApiClient::ProcessStdinRequest.new(data: data.to_s))
      nil
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to send process stdin')
    end

    # Close stdin for a running process.
    #
    # @return [nil]
    # @raise [Daytona::Sdk::Error] If EOF cannot be sent
    def stdin_eof
      toolbox_api.send_process_stdin(id, DaytonaToolboxApiClient::ProcessStdinRequest.new(eof: true))
      nil
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to close process stdin')
    end

    # Signal a running process, optionally escalating after a delay.
    #
    # @param signal [String] Initial signal
    # @param escalate_after_ms [Integer, nil] Delay before escalation, in milliseconds
    # @param escalate_to [String] Escalation signal
    # @return [nil]
    # @raise [Daytona::Sdk::Error] If the signal cannot be sent
    def kill(signal: 'SIGTERM', escalate_after_ms: nil, escalate_to: 'SIGKILL')
      request = { signal: }
      request.merge!(escalate_after_ms:, escalate_to:) unless escalate_after_ms.nil?
      toolbox_api.signal_process(id, DaytonaToolboxApiClient::KillProcessRequest.new(request))
      nil
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to signal process')
    end

    # Resize a PTY process terminal.
    #
    # This operation is only meaningful for processes created with `kind=pty`.
    #
    # @param cols [Integer] Terminal width in columns
    # @param rows [Integer] Terminal height in rows
    # @return [Object] Toolbox API response
    # @raise [Daytona::Sdk::Error] If the PTY cannot be resized
    def resize(cols:, rows:)
      toolbox_api.resize_process(id, DaytonaToolboxApiClient::ResizeProcessRequest.new(cols:, rows:))
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to resize process')
    end

    # Wait for the process to finish and return its terminal result.
    #
    # Call {#output} after waiting to collect retained stdout and stderr. A finite
    # timeout can return an unfinished result; it does not imply output collection.
    #
    # @param timeout_ms [Integer, nil] Maximum wait in milliseconds, or nil indefinitely
    # @return [DaytonaToolboxApiClient::ProcessResult] Current terminal or timeout result
    # @raise [Daytona::Sdk::Error] If waiting fails
    def wait(timeout_ms: nil)
      toolbox_api.wait_for_process(id, **{ timeout_ms: }.compact)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to wait for process')
    end

    # Collect retained stdout and stderr with the process terminal metadata.
    #
    # This pages logs from the available start and should normally be paired with
    # {#wait}. Collection fails or may be incomplete after the retention window ends.
    #
    # @return [Daytona::ProcessOutput] Decoded output and terminal metadata
    # @raise [Daytona::Sdk::Error] If process metadata or retained logs cannot be read
    def output
      record = get
      stdout, stderr = collect_output
      ProcessOutput.new(
        stdout:, stderr:, exit_code: record.exit_code, signal: record.signal, reason: record.reason
      )
    end

    # Clean up the process, its retained logs, and its resources.
    #
    # Processes started with `keep_logs: 'until_cleanup'` retain logs until this is
    # called. Cleanup is distinct from waiting for process completion.
    #
    # @return [nil]
    # @raise [Daytona::Sdk::Error] If cleanup fails
    def cleanup
      toolbox_api.cleanup_process(id)
      nil
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to clean up process')
    end

    # Attach an interactive terminal to a PTY process.
    #
    # This method is PTY-only and raises for processes whose `kind` is not `pty`.
    # The returned handle supports interactive input, resize, wait, and termination.
    #
    # @return [Daytona::PtyHandle] Connected interactive PTY handle
    # @raise [Daytona::Sdk::Error] If the process is not a PTY or attachment fails
    # @raise [Daytona::Sdk::ConnectionError] If the terminal socket cannot be connected
    def attach_terminal # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
      record = get
      raise Sdk::Error, 'Terminal attachment is only supported for kind=pty processes' unless record.kind == 'pty'

      socket = WebSocket::Client::Simple.connect(websocket_url, headers: websocket_headers)
      PtyHandle.new(
        socket,
        session_id: id,
        handle_resize: ->(size) { resize(cols: size.cols, rows: size.rows) },
        handle_kill: -> { kill }
      ).tap(&:wait_for_connection)
    rescue *Sdk::API_ERROR_CLASSES => e
      raise Sdk.wrap_error(e, 'Failed to attach process terminal')
    rescue *TRANSPORT_ERROR_CLASSES => e
      raise Sdk::ConnectionError, "Failed to attach process terminal: #{e.message}"
    end

    # Replay and decode all currently retained output.
    #
    # Prefer {#output} for terminal metadata as well as output.
    #
    # @return [Array<String>] Two elements: decoded stdout and stderr
    # @raise [Daytona::Sdk::Error] If retained logs cannot be read
    def collect_output # rubocop:disable Metrics/AbcSize, Metrics/CyclomaticComplexity, Metrics/MethodLength
      decoder = ProcessRunFrameDecoder.new
      chunks = { stdout: [], stderr: [] }
      cursor = 'start'

      10_000.times do
        page = logs(cursor:, limit: 1000, encoding: 'base64')
        Array(page.frames).each do |frame|
          channel, text = decoder.decode(channel: frame.channel, data: frame.data, encoding: frame.encoding)
          chunks[channel] << text if channel && !text.empty?
        end
        break if page.eof || Array(page.frames).empty?

        cursor = page.next_cursor
      end

      stdout_tail, stderr_tail = decoder.flush
      chunks[:stdout] << stdout_tail unless stdout_tail.empty?
      chunks[:stderr] << stderr_tail unless stderr_tail.empty?
      [chunks[:stdout].join, chunks[:stderr].join]
    end

    private

    attr_reader :toolbox_api

    def stream_sse(cursor:, encoding:) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
      uri = stream_uri(cursor:, encoding:)
      Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https', read_timeout: nil) do |http|
        request = Net::HTTP::Get.new(uri, toolbox_api.api_client.default_headers.merge('Accept' => 'text/event-stream'))
        http.request(request) do |response|
          unless response.is_a?(Net::HTTPSuccess)
            body = response.body.to_s
            raise DaytonaToolboxApiClient::ApiError.new(code: response.code.to_i, response_body: body)
          end

          each_sse_data(response) { |data| yield parse_stream_event(data) }
        end
      end
    end

    def each_sse_data(response)
      buffer = +''
      response.read_body do |chunk|
        buffer << chunk
        while (line_end = buffer.index("\n"))
          line = buffer.slice!(0..line_end).strip
          yield line.delete_prefix('data:').strip if line.start_with?('data:')
        end
      end
      line = buffer.strip
      yield line.delete_prefix('data:').strip if line.start_with?('data:')
    end

    def parse_stream_event(data)
      payload = JSON.parse(data)
      return { type: 'state', cursor: payload['cursor'], process: payload } if payload.key?('state')
      return { type: 'eof', cursor: payload['cursor'] } if payload.keys == ['cursor']
      return typed_stream_event(payload) if payload.key?('type')
      return warning_stream_event(payload) if payload.key?('message')

      frame = DaytonaToolboxApiClient::ProcessLogFrame.build_from_hash(payload)
      { type: 'log', cursor: frame.cursor, frame: }
    end

    def typed_stream_event(payload)
      type = payload['type']
      return log_stream_event(payload) if type == 'log'
      return { type:, cursor: payload['cursor'], process: payload['process'] || payload } if type == 'state'
      return warning_stream_event(payload) if type == 'warning'
      return { type:, cursor: payload['cursor'] } if type == 'eof'

      raise JSON::ParserError, "Unknown process stream event: #{type}"
    end

    def log_stream_event(payload)
      frame = DaytonaToolboxApiClient::ProcessLogFrame.build_from_hash(payload.fetch('frame'))
      { type: 'log', cursor: payload['cursor'] || frame.cursor, frame: }
    end

    def warning_stream_event(payload)
      {
        type: 'warning', cursor: payload['cursor'], message: payload['message'],
        first_available_cursor: payload['firstAvailableCursor']
      }
    end

    def stream_uri(cursor:, encoding:)
      path = "/processes/#{CGI.escapeURIComponent(id)}/logs"
      uri = URI(toolbox_api.api_client.build_request_url(path))
      uri.query = URI.encode_www_form({ follow: true, cursor:, encoding: }.compact)
      uri
    end

    def websocket_url
      uri = URI(toolbox_api.api_client.build_request_url("/processes/#{CGI.escapeURIComponent(id)}/attach"))
      uri.scheme = uri.scheme == 'https' ? 'wss' : 'ws'
      uri.to_s
    end

    def websocket_headers
      headers = toolbox_api.api_client.default_headers.dup
      headers['Sec-WebSocket-Protocol'] =
        [headers['Sec-WebSocket-Protocol'], PTY_EXIT_CONTROL_SUBPROTOCOL].compact.join(', ')
      headers
    end
  end
end
