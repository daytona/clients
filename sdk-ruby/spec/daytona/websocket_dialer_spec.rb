# frozen_string_literal: true

# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

require 'openssl'
require 'securerandom'
require 'socket'
require 'tempfile'
require 'timeout'

# Issues short-lived EC certificates in-process; no fixture files on disk.
class CertFactory
  Pair = Struct.new(:cert, :key)

  def self.keypair
    OpenSSL::PKey::EC.generate('prime256v1')
  end

  def self.issue(subject:, key:, issuer: nil, authority: false, san: nil) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
    crt = OpenSSL::X509::Certificate.new
    crt.version = 2
    crt.serial = SecureRandom.random_number(2**64)
    crt.subject = OpenSSL::X509::Name.parse(subject)
    crt.issuer = issuer ? issuer.cert.subject : crt.subject
    crt.public_key = key
    crt.not_before = Time.now - 3600
    crt.not_after = Time.now + 3600
    ef = OpenSSL::X509::ExtensionFactory.new
    ef.subject_certificate = crt
    ef.issuer_certificate = issuer ? issuer.cert : crt
    crt.add_extension ef.create_extension('basicConstraints', authority ? 'CA:TRUE' : 'CA:FALSE', true)
    crt.add_extension ef.create_extension('subjectAltName', san) if san
    crt.sign(issuer ? issuer.key : key, OpenSSL::Digest.new('SHA256'))
    Pair.new(crt, key)
  end
end

# Ephemeral TLS listener that records the request bytes it received.
class RecordingTlsServer
  UPGRADE = "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n" \
            "Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n"

  attr_reader :port

  def initialize(pair) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
    ctx = OpenSSL::SSL::SSLContext.new
    ctx.cert = pair.cert
    ctx.key = pair.key
    tcp = TCPServer.new('127.0.0.1', 0)
    @port = tcp.addr[1]
    @server = OpenSSL::SSL::SSLServer.new(tcp, ctx)
    @server.start_immediately = true
    @seen = Queue.new
    @frames = Queue.new
    @sockets = []
    @thread = Thread.new { serve }
  end

  def serve
    loop do
      sock = @server.accept
      @seen << sock.readpartial(4096)
      sock.write(UPGRADE)
      @sockets << sock
      loop { @frames << sock.readpartial(4096) }
    rescue StandardError => e
      @seen << "handshake-rejected: #{e.class}"
    end
  end

  # Decodes the next client frame, so a caller can prove the inherited #send
  # actually put bytes on the wire rather than silently no-opping.
  def next_frame(wait: 2)
    raw = @frames.pop(timeout: wait)
    return nil if raw.nil?

    incoming = WebSocket::Frame::Incoming::Server.new(version: 13)
    incoming << raw
    incoming.next&.to_s
  end

  # Blocks for the first event before draining. A non-blocking drain races the
  # server thread: when the dial fails fast the queue is still empty, so a
  # "credential absent" assertion would pass without ever observing the wire.
  # On a refusal the thread still pushes its handshake-rejected marker, so the
  # bounded pop always terminates.
  def received(wait: 2)
    first = @seen.pop(timeout: wait)
    return '' if first.nil?

    chunks = [first]
    begin
      loop { chunks << @seen.pop(true) }
    rescue ThreadError
      nil
    end
    chunks.join
  end

  def stop
    @thread.kill
    @sockets.each do |s|
      s.close
    rescue StandardError
      nil
    end
    @server.close
  rescue StandardError
    nil
  end
end

# Real-TLS regression coverage for the credential-bearing wss:// dials.
#
# These examples do NOT stub the dial: they stand up a local TLS listener on
# 127.0.0.1 and assert on the bytes the peer actually received. The property
# under test is "no credential reaches an unverified peer", not merely "an
# exception was raised" - a verify_mode-only fix raises in the self-signed case
# and silently leaks in the wrong-hostname case.
RSpec.describe Daytona::Common::WebSocketDialer do
  let(:ca) { CertFactory.issue(subject: '/CN=Daytona Spec CA', key: CertFactory.keypair, authority: true) }
  let(:valid_leaf) do
    CertFactory.issue(subject: '/CN=localhost', key: CertFactory.keypair, issuer: ca, san: 'DNS:localhost')
  end
  let(:wrong_host_leaf) do
    CertFactory.issue(subject: '/CN=evil.example', key: CertFactory.keypair, issuer: ca, san: 'DNS:evil.example')
  end
  let(:self_signed_leaf) do
    CertFactory.issue(subject: '/CN=localhost', key: CertFactory.keypair, san: 'DNS:localhost')
  end

  # The preview token is what legitimately rides these dials after the
  # allowlist, so it is the credential whose leakage matters on the wire.
  let(:preview_token) { 'SPEC-PREVIEW-TOKEN' }
  let(:credential) { 'Bearer SPEC-ORG-API-KEY' }

  # The client must trust only the spec CA. The dialer builds a fresh
  # OpenSSL::X509::Store and calls #set_default_paths per dial, which honours
  # SSL_CERT_FILE at dial time. A dialer relying on SSLContext#set_params alone
  # would inherit the process-wide DEFAULT_CERT_STORE (cached when openssl was
  # required) and ignore this - the positive control detects that, because the
  # wrong-hostname case would then be refused for unknown-CA reasons and stop
  # discriminating between a real fix and a verify_mode-only fix.
  around do |example|
    ca_file = Tempfile.new(['spec-ca', '.pem'])
    ca_file.write(ca.cert.to_pem)
    ca_file.flush
    previous = ENV.fetch('SSL_CERT_FILE', nil)
    ENV['SSL_CERT_FILE'] = ca_file.path
    example.run
  ensure
    previous ? ENV['SSL_CERT_FILE'] = previous : ENV.delete('SSL_CERT_FILE')
    ca_file&.close!
  end

  # Bounded so an unfixed tree fails fast rather than hanging on the reader thread.
  def dial(port, headers)
    Timeout.timeout(5) do
      described_class.connect("wss://localhost:#{port}/process/pty/pty-1/connect", headers: headers)
    end
    nil
  rescue StandardError => e
    e
  end

  describe 'peer verification' do
    it 'refuses a self-signed peer and writes no credential' do
      server = RecordingTlsServer.new(self_signed_leaf)

      error = dial(server.port, 'X-Daytona-Preview-Token' => preview_token)

      # Leak assertion first: on an unfixed tree this is the failure message,
      # so the report names the security defect rather than a missing exception.
      expect(server.received).not_to include(preview_token)
      expect(error).to be_a(OpenSSL::SSL::SSLError)
    ensure
      server&.stop
    end

    # A verify_mode-only fix FAILS this example: the chain is valid, only the
    # name is wrong, and websocket-client-simple never checks the name.
    it 'refuses a CA-signed peer presenting the wrong hostname and writes no credential' do
      server = RecordingTlsServer.new(wrong_host_leaf)

      error = dial(server.port, 'X-Daytona-Preview-Token' => preview_token)

      expect(server.received).not_to include(preview_token)
      expect(error).to be_a(OpenSSL::SSL::SSLError)
    ensure
      server&.stop
    end

    it 'never writes the organization credential, even when a caller passes one' do
      server = RecordingTlsServer.new(valid_leaf)

      dial(server.port, 'Authorization' => credential, 'X-Daytona-Preview-Token' => preview_token)

      wire = server.received
      expect(wire).to include(preview_token)
      expect(wire).not_to include('SPEC-ORG-API-KEY')
    ensure
      server&.stop
    end

    # Positive control. Without it both refusals could pass simply because TLS
    # is broken or the CA is untrusted.
    it 'completes the upgrade against a valid peer' do
      server = RecordingTlsServer.new(valid_leaf)

      error = dial(server.port, 'X-Daytona-Preview-Token' => preview_token)

      expect(error).to be_nil
      expect(server.received).to include(preview_token)
    ensure
      server&.stop
    end
  end

  # The reimplemented #connect is responsible for every instance variable the
  # inherited #send, #close, #open? and #closed? read. Nothing else in the suite
  # touches the returned client, so without this example a parent-side rename in
  # a gem bump would leave #send a permanent no-op - writing nothing, raising
  # nothing - and the suite would stay green.
  describe 'inherited client contract' do
    it 'completes a send and close round trip against a real peer' do
      server = RecordingTlsServer.new(valid_leaf)
      client = nil

      Timeout.timeout(5) do
        client = described_class.connect("wss://localhost:#{server.port}/x",
                                         headers: { 'X-Daytona-Preview-Token' => preview_token })
        server.received
        sleep 0.05 until client.open?
      end

      client.send('ROUND-TRIP')
      frame = server.next_frame

      expect(frame).to eq('ROUND-TRIP')
      expect { client.close }.to change(client, :closed?).from(false).to(true)
    ensure
      server&.stop
    end
  end

  describe '.safe_headers' do
    let(:default_headers) do
      {
        'Authorization' => credential,
        'X-Daytona-Organization-ID' => 'org-123',
        'X-Daytona-Source' => 'sdk-ruby',
        'X-Daytona-SDK-Version' => '0.0.0',
        'User-Agent' => 'sdk-ruby/0.0.0'
      }
    end

    it 'drops the organization credential' do
      expect(described_class.safe_headers(default_headers)).not_to include('Authorization')
    end

    it 'drops the organization id' do
      expect(described_class.safe_headers(default_headers)).not_to include('X-Daytona-Organization-ID')
    end

    it 'preserves the headers the server reads' do
      expect(described_class.safe_headers(default_headers)).to eq(
        'X-Daytona-Source' => 'sdk-ruby',
        'X-Daytona-SDK-Version' => '0.0.0',
        'User-Agent' => 'sdk-ruby/0.0.0'
      )
    end

    # Allowlist rather than denylist: a credential added to default_headers in
    # future must be excluded by default instead of silently riding the dial.
    it 'excludes headers it does not recognise' do
      expect(described_class.safe_headers('X-Future-Credential' => 'secret')).to be_empty
    end
  end

  # Structural guard: any new credential-bearing dial must route through the
  # shared dialer. This turns a future point-dial into a CI failure rather than
  # a silent reintroduction of the defect.
  describe 'dial routing' do
    it 'has no direct WebSocket::Client::Simple.connect outside the shared dialer' do
      root = File.expand_path('../../lib', __dir__)
      offenders = Dir.glob("#{root}/**/*.rb").select do |file|
        File.read(file).match?(/^\s*[^#\n]*WebSocket::Client::Simple\.connect/)
      end

      # The dialer itself subclasses WebSocket::Client::Simple::Client and never
      # calls .connect on the module, so it is not expected here.
      expect(offenders.map { |file| file.delete_prefix("#{root}/") })
        .to contain_exactly(
          # TODO: migrate onto the shared dialer; its token is sent post-verification.
          'daytona/common/socketio_client.rb'
        )
    end
  end
end
