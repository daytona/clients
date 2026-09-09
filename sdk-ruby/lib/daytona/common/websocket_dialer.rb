# frozen_string_literal: true

# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

require 'openssl'
require 'socket'
require 'uri'
require 'websocket'
require 'websocket-client-simple'

module Daytona
  module Common
    # Establishes WebSocket connections with the peer certificate fully verified
    # before any request bytes are written.
    #
    # `websocket-client-simple` builds its own `SSLContext` and never calls
    # `OpenSSL::SSL::SSLContext#set_params`, so it does not pick up Ruby's own
    # defaults (`verify_mode: VERIFY_PEER`, `verify_hostname: true`). It applies
    # `verify_mode` only when the caller passes it, and offers no way to enable
    # hostname verification at all. Because the context is frozen by
    # `SSLSocket.new` and the handshake is written before `connect` returns,
    # there is no caller-side hook to correct this - so the dial itself has to
    # be owned here.
    #
    # Chain verification alone is not sufficient: without hostname verification
    # a peer holding any valid certificate can terminate the connection and
    # receive the request headers.
    module WebSocketDialer
      # Opens a verified WebSocket connection.
      #
      # Mirrors `WebSocket::Client::Simple.connect`: the block, if given, receives
      # the client before the connection is established so handlers can be
      # registered, and the client is returned.
      #
      # @param url [String] The `ws://` or `wss://` URL to dial.
      # @param options [Hash] Passed through to the client; `:headers`, `:ssl_version` and `:cert_store` are honoured.
      # @return [VerifyingClient] The connected client.
      # @raise [OpenSSL::SSL::SSLError] If the peer certificate fails chain or hostname verification.
      def self.connect(url, options = {})
        client = VerifyingClient.new
        yield client if block_given?
        client.connect(url, options)
        client
      end

      # A `websocket-client-simple` client that verifies the peer before writing.
      #
      # `#connect` is reimplemented rather than extended because the upstream
      # method builds the socket, performs the TLS handshake and writes the
      # request in one pass, and returns early when `@socket` is already set.
      #
      # Everything after TLS setup mirrors upstream so that framing, the reader
      # thread and event semantics stay identical. That couples to the instance
      # variables the inherited `#send`, `#close`, `#open?` and `#closed?` read,
      # so the gemspec pins the gem to `~> 0.9.0` - a 0.x minor bump could rename
      # them. `websocket_dialer_spec.rb` drives a full send/close round trip
      # against a real listener, which fails if that contract breaks.
      class VerifyingClient < WebSocket::Client::Simple::Client
        # @param url [String] The `ws://` or `wss://` URL to dial.
        # @param options [Hash] Connection options.
        # @return [void]
        def connect(url, options = {})
          return if @socket

          @url = url
          uri = URI.parse(url)
          @socket = TCPSocket.new(uri.host, uri.port || (uri.scheme == 'wss' ? 443 : 80))
          @socket = verified_ssl_socket(@socket, uri, options) if %w[https wss].include?(uri.scheme)

          start_websocket(url, options)
        end

        private

        # Wraps a socket in TLS with the peer certificate verified.
        #
        # @param socket [TCPSocket] The connected plaintext socket.
        # @param uri [URI] The dialed URI, whose host the certificate must match.
        # @param options [Hash] Connection options.
        # @return [OpenSSL::SSL::SSLSocket] The connected, verified socket.
        # @raise [OpenSSL::SSL::SSLError] If chain or hostname verification fails.
        def verified_ssl_socket(socket, uri, options) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
          ctx = OpenSSL::SSL::SSLContext.new
          ctx.ssl_version = options[:ssl_version] if options[:ssl_version]

          # Only seed system roots into a store we own. Adding them to a
          # caller-supplied store would silently widen their trust policy.
          cert_store = options[:cert_store]
          unless cert_store
            cert_store = OpenSSL::X509::Store.new
            cert_store.set_default_paths
          end
          ctx.cert_store = cert_store

          # Must precede SSLSocket.new, which freezes the context.
          ctx.verify_mode = OpenSSL::SSL::VERIFY_PEER
          ctx.verify_hostname = true

          ssl = OpenSSL::SSL::SSLSocket.new(socket, ctx)
          ssl.sync_close = true
          ssl.hostname = uri.host
          begin
            ssl.connect
            # Redundant while verify_hostname holds, but keeps the check explicit
            # and independent of the context surviving future changes.
            ssl.post_connection_check(uri.host)
          rescue StandardError
            ssl.close
            raise
          end
          ssl
        end

        # Performs the WebSocket handshake and starts the reader thread.
        #
        # Mirrors `WebSocket::Client::Simple::Client#connect` from the request
        # onward. Only reached once the socket is verified.
        #
        # @param url [String] The dialed URL.
        # @param options [Hash] Connection options.
        # @return [void]
        def start_websocket(url, options) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
          WebSocket.should_raise = true
          @handshake = WebSocket::Handshake::Client.new(url: url, headers: options[:headers])
          @handshaked = false
          @pipe_broken = false
          @closed = false
          frame = WebSocket::Frame::Incoming::Client.new

          once :__close do |err|
            close
            emit :close, err
          end

          @thread = Thread.new do
            until @closed
              begin
                unless (recv_data = @socket.getc)
                  sleep 1
                  next
                end
                if @handshaked
                  frame << recv_data
                  while (msg = frame.next)
                    emit :message, msg
                  end
                else
                  @handshake << recv_data
                  if @handshake.finished?
                    @handshaked = true
                    emit :open
                  end
                end
              rescue StandardError => e
                emit :error, e
              end
            end
          end

          @socket.write @handshake.to_s
        end
      end
    end
  end
end
