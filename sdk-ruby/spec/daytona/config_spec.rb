# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'stringio'
require 'tmpdir'

RSpec.describe Daytona::Config do
  around do |example|
    env_keys = %w[
      DAYTONA_API_KEY
      DAYTONA_JWT_TOKEN
      DAYTONA_API_URL
      DAYTONA_TARGET
      DAYTONA_ORGANIZATION_ID
      DAYTONA_USE_DEPRECATED_POLLING
      DAYTONA_CUSTOM_VAR
    ]
    saved = env_keys.to_h { |key| [key, ENV.delete(key)] }
    example.run
  ensure
    saved.each { |key, value| value ? ENV[key] = value : ENV.delete(key) }
  end

  describe '#initialize' do
    it 'accepts explicit api_key' do
      config = described_class.new(api_key: 'my-key')

      expect(config.api_key).to eq('my-key')
    end

    it 'accepts explicit jwt_token and organization_id' do
      config = described_class.new(jwt_token: 'jwt-tok', organization_id: 'org-42')

      expect(config.jwt_token).to eq('jwt-tok')
      expect(config.organization_id).to eq('org-42')
    end

    it 'defaults api_url to API_URL constant' do
      config = described_class.new(api_key: 'k')

      expect(config.api_url).to eq(described_class::API_URL)
    end

    it 'reads values from ENV when explicit args are missing' do
      ENV['DAYTONA_API_KEY'] = 'env-key'
      ENV['DAYTONA_API_URL'] = 'https://custom.api'
      ENV['DAYTONA_TARGET'] = 'eu'
      ENV['DAYTONA_ORGANIZATION_ID'] = 'org-env'

      config = described_class.new

      expect(config.api_key).to eq('env-key')
      expect(config.api_url).to eq('https://custom.api')
      expect(config.target).to eq('eu')
      expect(config.organization_id).to eq('org-env')
    end

    it 'prefers explicit params over ENV' do
      ENV['DAYTONA_API_KEY'] = 'env-key'

      config = described_class.new(api_key: 'explicit-key')

      expect(config.api_key).to eq('explicit-key')
    end

    it 'reads .env and .env.local without mutating ENV and prefers .env.local', :real_dotenv do
      Dir.mktmpdir do |dir|
        File.write(File.join(dir, '.env'), <<~ENVFILE)
          DAYTONA_API_KEY=env-file-key
          DAYTONA_TARGET=us
          NOT_DAYTONA=ignored
        ENVFILE
        File.write(File.join(dir, '.env.local'), <<~ENVFILE)
          DAYTONA_API_KEY=env-local-key
          DAYTONA_API_URL=https://local.api
        ENVFILE

        Dir.chdir(dir) do
          captured = StringIO.new
          original = $stderr
          $stderr = captured
          # The endpoint is deliberately excluded from dotenv precedence, so the
          # `https://local.api` value in .env.local is reported and not used.
          begin
            config = described_class.new
          ensure
            $stderr = original
          end
          expect(captured.string).to include('was ignored')

          expect(config.api_key).to eq('env-local-key')
          expect(config.target).to eq('us')
          expect(config.api_url).to eq(Daytona::Config::API_URL)
          expect(ENV.fetch('DAYTONA_API_KEY', nil)).to be_nil
        end
      end
    end

    it 'stores experimental config' do
      config = described_class.new(api_key: 'k', _experimental: { 'otel_enabled' => true })

      expect(config._experimental).to eq({ 'otel_enabled' => true })
    end

    it 'defaults use_deprecated_polling to false' do
      config = described_class.new(api_key: 'k')

      expect(config.use_deprecated_polling).to be(false)
    end

    it 'reads use_deprecated_polling from ENV when explicit args are missing' do
      ENV['DAYTONA_USE_DEPRECATED_POLLING'] = 'true'

      config = described_class.new(api_key: 'k')

      expect(config.use_deprecated_polling).to be(true)
    end

    it 'keeps use_deprecated_polling disabled when explicit config is false even if ENV is true' do
      ENV['DAYTONA_USE_DEPRECATED_POLLING'] = 'true'

      config = described_class.new(api_key: 'k', use_deprecated_polling: false)

      expect(config.use_deprecated_polling).to be(false)
    end
  end

  describe '#read_env' do
    it 'returns values for DAYTONA_-prefixed variables from ENV' do
      ENV['DAYTONA_CUSTOM_VAR'] = 'hello'
      config = described_class.new(api_key: 'k')

      expect(config.read_env('DAYTONA_CUSTOM_VAR')).to eq('hello')
    end

    it 'returns nil for unset DAYTONA_ variables' do
      config = described_class.new(api_key: 'k')

      expect(config.read_env('DAYTONA_NONEXISTENT')).to be_nil
    end

    it 'raises ArgumentError for non-DAYTONA_ variable names' do
      config = described_class.new(api_key: 'k')

      expect { config.read_env('OTHER_VAR') }
        .to raise_error(ArgumentError, /Variable must start with 'DAYTONA_'/)
    end
  end

  describe 'endpoint trust separation', :real_dotenv do
    # A .env in the working directory must not decide where the credential is sent: the
    # working directory is frequently a cloned repository, authored by a third party.
    around do |example|
      Dir.mktmpdir do |dir|
        Dir.chdir(dir) { example.run }
      end
    end

    # Construction must happen OUTSIDE an output matcher: if the report is missing, the
    # example has to still reach the endpoint assertion, or a regression is diagnosed as
    # "no warning" when the real defect is the credential's destination.
    def build_config(**kwargs)
      captured = StringIO.new
      original = $stderr
      $stderr = captured
      begin
        [described_class.new(**kwargs), captured.string]
      ensure
        $stderr = original
      end
    end

    def ignored_endpoint_report?(stderr)
      stderr.include?('`DAYTONA_API_URL`') && stderr.include?('was ignored')
    end

    it 'ignores DAYTONA_API_URL supplied by a .env in the working directory' do
      File.write('.env', "DAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config(api_key: 'victim-key')

      expect(config.api_url).to eq(Daytona::Config::API_URL)
      expect(config.api_key).to eq('victim-key')
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'ignores DAYTONA_API_URL supplied by a .env.local in the working directory' do
      File.write('.env.local', "DAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config(api_key: 'victim-key')

      expect(config.api_url).to eq(Daytona::Config::API_URL)
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'ignores a dotenv endpoint even when the same file supplies the credential' do
      File.write('.env', "DAYTONA_API_KEY=attacker-key\nDAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config

      expect(config.api_url).to eq(Daytona::Config::API_URL)
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'still lets the process environment set the endpoint' do
      ENV['DAYTONA_API_URL'] = 'https://chosen-by-env.example/api'
      File.write('.env', "DAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config(api_key: 'victim-key')

      expect(config.api_url).to eq('https://chosen-by-env.example/api')
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'still lets an explicit api_url set the endpoint' do
      File.write('.env', "DAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config(api_key: 'victim-key', api_url: 'https://chosen.example/api')

      expect(config.api_url).to eq('https://chosen.example/api')
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'still reports a hostile dotenv to a fully configured client' do
      File.write('.env', "DAYTONA_API_URL=http://attacker.example/api\n")

      config, stderr = build_config(api_key: 'victim-key', api_url: 'https://chosen.example/api', target: 'us')

      expect(config.api_url).to eq('https://chosen.example/api')
      expect(ignored_endpoint_report?(stderr)).to be(true)
    end

    it 'stays quiet for the documented .env layout and still reads the credential from it' do
      File.write('.env', "DAYTONA_API_KEY=victim-key\nDAYTONA_API_URL=#{Daytona::Config::API_URL}\n")

      config, stderr = build_config

      expect(config.api_url).to eq(Daytona::Config::API_URL)
      expect(config.api_key).to eq('victim-key')
      expect(stderr).to eq('')
    end
  end
end
