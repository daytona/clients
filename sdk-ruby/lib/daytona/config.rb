# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'dotenv'

module Daytona
  # Dotenv runs `$(...)` in a value through the shell while parsing, and the working
  # directory is frequently a cloned repository, so parsing `.env` there would execute
  # whatever its author put in it.
  module EnvFile
    # Dotenv::Parser picks its substitutions up from `self.class.substitutions`, and Ruby
    # does not inherit class-level instance variables, so declaring the list here drops
    # command substitution while leaving Dotenv::Parser alone — a host application's own
    # Dotenv.load keeps the behaviour its author chose. `${VAR}` is kept: it reads only
    # keys already parsed or the process environment.
    class Parser < Dotenv::Parser
      @substitutions = [Dotenv::Substitutions::Variable].freeze
    end

    # Read with the mode dotenv itself uses, so the accepted format does not narrow: `bom`
    # skips a byte-order mark an editor on Windows may have written, and pinning `utf-8`
    # keeps the file readable under a POSIX locale, where the default external encoding
    # would make one accented byte anywhere — a comment included — raise while it is scanned.
    def self.parse(path)
      verify_suppression!
      Parser.call(File.read(path, mode: 'rb:bom|utf-8'))
    end

    # The subclass reaches into dotenv's internals rather than a public API, and dotenv has
    # already reorganised them once inside the range the gemspec allows, so confirm the
    # suppression actually holds in the process that relies on it rather than trusting the
    # version pinned in CI. Checked here rather than on load: this is the operation the
    # guard protects, so a lapse stops it, and a gem that never reads a .env still loads.
    # A failure is not memoised, so it is raised again on the next attempt.
    def self.verify_suppression!
      return if @verified

      probe = Parser.call('DAYTONA_PROBE=$(echo substituted)')['DAYTONA_PROBE']
      raise "dotenv command substitution is not suppressed (got #{probe.inspect})" unless
        probe == '$(echo substituted)'

      @verified = true
    end
  end

  class Config
    API_URL = 'https://app.daytona.io/api'

    # API key for authentication with the Daytona API
    #
    # @return [String, nil] Daytona API key
    attr_accessor :api_key

    # JWT token for authentication with the Daytona API
    #
    # @return [String, nil] Daytona JWT token
    attr_accessor :jwt_token

    # URL of the Daytona API
    #
    # @return [String, nil] Daytona API URL
    attr_accessor :api_url

    # Organization ID for authentication with the Daytona API
    #
    # @return [String, nil] Daytona API URL
    attr_accessor :organization_id

    # Target environment for sandboxes
    #
    # @return [String, nil] Daytona target
    attr_accessor :target

    # Enable OpenTelemetry tracing for SDK operations.
    #
    # @return [Boolean, nil]
    attr_accessor :otel_enabled

    # Observe sandbox state by legacy polling instead of WebSocket event streaming.
    # Defaults to false (event streaming). Can also be enabled via the
    # DAYTONA_USE_DEPRECATED_POLLING environment variable.
    #
    # @deprecated Polling-only mode will be removed in a future release;
    #   event streaming is the default and falls back to polling automatically
    #   when WebSockets are unavailable.
    # @return [Boolean]
    attr_accessor :use_deprecated_polling

    # Experimental configuration options
    #
    # @return [Hash, nil] Experimental configuration hash
    attr_accessor :_experimental

    # Initializes a new Daytona::Config object.
    #
    # @param api_key [String, nil] Daytona API key. Defaults to ENV['DAYTONA_API_KEY'].
    # @param jwt_token [String, nil] Daytona JWT token. Defaults to ENV['DAYTONA_JWT_TOKEN'].
    # @param api_url [String, nil] Daytona API URL. Defaults to ENV['DAYTONA_API_URL'] or Daytona::Config::API_URL.
    # @param organization_id [String, nil] Daytona organization ID. Defaults to ENV['DAYTONA_ORGANIZATION_ID'].
    # @param target [String, nil] Daytona target. Defaults to ENV['DAYTONA_TARGET'].
    # @param otel_enabled [Boolean, nil] Enable OpenTelemetry tracing for SDK operations.
    # @param use_deprecated_polling [Boolean, nil] Observe sandbox state by legacy polling instead of
    #   WebSocket event streaming. Defaults to false (event streaming). Can also be enabled via the
    #   DAYTONA_USE_DEPRECATED_POLLING environment variable.
    # @param _experimental [Hash, nil] Experimental configuration options.
    def initialize( # rubocop:disable Metrics/ParameterLists
      api_key: nil,
      jwt_token: nil,
      api_url: nil,
      organization_id: nil,
      target: nil,
      otel_enabled: nil,
      use_deprecated_polling: nil,
      _experimental: nil
    )
      @env_reader = daytona_env_reader

      @api_key = api_key || @env_reader.call('DAYTONA_API_KEY')
      @jwt_token = jwt_token || @env_reader.call('DAYTONA_JWT_TOKEN')
      # Resolved from the process environment only, never from .env / .env.local:
      # the endpoint decides where the credential above is sent.
      @api_url = resolve_api_url(api_url)
      @target = target || @env_reader.call('DAYTONA_TARGET')
      @organization_id = organization_id || @env_reader.call('DAYTONA_ORGANIZATION_ID')
      @otel_enabled = otel_enabled
      @_experimental = _experimental
      @use_deprecated_polling = resolve_use_deprecated_polling(use_deprecated_polling)
    end

    # Reads a DAYTONA_-prefixed environment variable using the same precedence
    # as the Config initializer: runtime ENV first, then .env.local, then .env.
    # Only names starting with DAYTONA_ are accepted.
    #
    # @param name [String] The environment variable name. Must start with DAYTONA_.
    # @return [String, nil] The value of the environment variable, or nil if not set.
    # @raise [ArgumentError] If name does not start with DAYTONA_.
    def read_env(name)
      @env_reader.call(name)
    end

    private

    # Resolves the API endpoint without consulting the working directory, then reports a
    # dotenv value that was passed over. Staying silent when the file value matches the
    # endpoint in use keeps the documented .env layout quiet, while a file that would have
    # changed the destination is surfaced.
    def resolve_api_url(api_url)
      resolved = api_url || process_env('DAYTONA_API_URL') || API_URL
      file_value = env_file_vars['DAYTONA_API_URL']
      return resolved if file_value.nil? || file_value.empty? || file_value == resolved

      warn(
        '`DAYTONA_API_URL` set in a .env or .env.local file was ignored: the Daytona API endpoint ' \
        'is never read from dotenv files, because the working directory is not always authored ' \
        "by you. Using `#{resolved}` instead. To change the endpoint, pass `api_url:` to " \
        '`Daytona::Config.new` or set `DAYTONA_API_URL` in the environment of the process.'
      )
      resolved
    end

    # Parses DAYTONA_-prefixed vars out of .env and .env.local in the working directory.
    # These files are not necessarily authored by whoever runs the process, so anything
    # that determines where a credential is sent must not be read from them.
    def env_file_vars
      @env_file_vars ||= parse_dotenv_files
    end

    def parse_dotenv_files
      file_vars = {}
      env_file = File.join(Dir.pwd, '.env')
      file_vars.merge!(daytona_filter(EnvFile.parse(env_file))) if File.exist?(env_file)
      env_local_file = File.join(Dir.pwd, '.env.local')
      file_vars.merge!(daytona_filter(EnvFile.parse(env_local_file))) if File.exist?(env_local_file)
      file_vars
    end

    # Returns a lambda that looks up DAYTONA_-prefixed env vars without writing to ENV.
    # Files are parsed once; lookups check runtime env first, then .env.local, then .env.
    def daytona_env_reader
      file_vars = env_file_vars

      lambda do |name|
        raise ArgumentError, "Variable must start with 'DAYTONA_', got '#{name}'" unless name.start_with?('DAYTONA_')

        ENV.key?(name) ? ENV[name] : file_vars[name]
      end
    end

    # Reads a DAYTONA_-prefixed variable from the process environment only.
    def process_env(name)
      raise ArgumentError, "Variable must start with 'DAYTONA_', got '#{name}'" unless name.start_with?('DAYTONA_')

      ENV.fetch(name, nil)
    end

    def daytona_filter(env_hash)
      env_hash.select { |k, _| k.start_with?('DAYTONA_') }
    end

    def resolve_use_deprecated_polling(use_deprecated_polling)
      return use_deprecated_polling unless use_deprecated_polling.nil?

      @env_reader.call('DAYTONA_USE_DEPRECATED_POLLING') == 'true'
    end
  end
end
