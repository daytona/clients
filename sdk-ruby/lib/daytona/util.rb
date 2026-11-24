# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'net/http'

module Daytona
  module Util
    STDOUT_PREFIX = "\x01\x01\01"
    private_constant :STDOUT_PREFIX

    STDERR_PREFIX = "\x02\x02\02"
    private_constant :STDERR_PREFIX

    PREFIX_LEN = STDOUT_PREFIX.bytesize
    private_constant :PREFIX_LEN

    # Splits one complete multiplexed log into stdout and stderr.
    # Use {Demuxer} for a log that arrives in several chunks.
    #
    # @param line [String]
    # @return [Array(String, String)] stdout and stderr
    def self.demux(line)
      demuxer = Demuxer.new
      stdout, stderr = demuxer.feed(line)
      rest_stdout, rest_stderr = demuxer.flush
      [stdout + rest_stdout, stderr + rest_stderr]
    end

    # Splits a multiplexed log stream into stdout and stderr chunk by chunk.
    #
    # The current stream and a trailing, possibly partial, stream marker carry over
    # from one chunk to the next, so the output does not depend on where the stream
    # is split (for example into WebSocket messages). Output before the first marker
    # of the whole stream is dropped.
    class Demuxer
      MARKERS = { stdout: STDOUT_PREFIX.b.freeze, stderr: STDERR_PREFIX.b.freeze }.freeze
      private_constant :MARKERS

      def initialize
        @stream = nil
        @pending = ''.b
        @encoding = Encoding::UTF_8
        @output = { stdout: ''.b, stderr: ''.b }
      end

      # @param data [String] The next chunk of the stream
      # @return [Array(String, String)] stdout and stderr decoded from the chunk so far
      def feed(data)
        @encoding = data.encoding
        buffer = @pending + data.b
        pos = consume_markers(buffer)
        held = partial_marker_length(buffer, pos)
        append(buffer.byteslice(pos, buffer.bytesize - pos - held))
        @pending = buffer.byteslice(buffer.bytesize - held, held)
        take_output
      end

      # Emits the bytes held back at the end of the stream as output of the current stream.
      #
      # @return [Array(String, String)] stdout and stderr
      def flush
        append(@pending)
        @pending = ''.b
        take_output
      end

      private

      def consume_markers(buffer)
        pos = 0
        loop do
          marker_at, stream = next_marker(buffer, pos)
          return pos unless marker_at

          append(buffer.byteslice(pos, marker_at - pos))
          @stream = stream
          pos = marker_at + PREFIX_LEN
        end
      end

      def next_marker(buffer, pos)
        MARKERS.filter_map { |stream, marker| (at = buffer.index(marker, pos)) && [at, stream] }.min_by(&:first)
      end

      def partial_marker_length(buffer, pos)
        (PREFIX_LEN - 1).downto(1).find do |length|
          next false if buffer.bytesize - pos < length

          tail = buffer.byteslice(buffer.bytesize - length, length)
          MARKERS.each_value.any? { |marker| marker.start_with?(tail) }
        end || 0
      end

      def append(chunk)
        @output[@stream] << chunk if @stream
      end

      def take_output
        output = @output.values.map { |part| part.force_encoding(@encoding) }
        @output = { stdout: ''.b, stderr: ''.b }
        output
      end
    end

    # @param uri [URI]
    # @param on_chunk [Proc]
    # @param headers [Hash<String, String>]
    # @return [Thread]
    def self.stream_async(uri:, on_chunk:, headers: nil) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
      Sdk.logger.debug("Starting async stream: #{uri}")
      Thread.new do
        Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https') do |http|
          request = Net::HTTP::Get.new(uri, headers)

          http.request(request) do |response|
            response.read_body do |chunk|
              Sdk.logger.debug("Chunked response received: #{chunk.inspect}")
              on_chunk.call(chunk)
            end
          end
        end
      rescue Net::ReadTimeout => e
        Sdk.logger.debug("Async stream (#{uri}) timeout: #{e.inspect}")
      end
    end
  end
end
