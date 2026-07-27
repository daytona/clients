# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

RSpec.describe Daytona::Sdk do
  describe 'error hierarchy' do
    it 'roots every typed error at Daytona::Sdk::Error' do
      [
        described_class::ValidationError,
        described_class::AuthenticationError,
        described_class::ForbiddenError,
        described_class::NotFoundError,
        described_class::TimeoutError,
        described_class::ConflictError,
        described_class::GoneError,
        described_class::UnprocessableEntityError,
        described_class::RateLimitError,
        described_class::ServerError,
        described_class::InternalServerError,
        described_class::BadGatewayError,
        described_class::ServiceUnavailableError,
        described_class::ConnectionError,
        described_class::ConnectionTimeoutError
      ].each do |cls|
        expect(cls < described_class::Error).to be(true), "#{cls} should inherit from Error"
      end
    end

    it 'makes 5xx subclasses inherit from ServerError' do
      expect(described_class::InternalServerError < described_class::ServerError).to be(true)
      expect(described_class::BadGatewayError < described_class::ServerError).to be(true)
      expect(described_class::ServiceUnavailableError < described_class::ServerError).to be(true)
    end

    it 'makes ConnectionTimeoutError inherit from ConnectionError' do
      expect(described_class::ConnectionTimeoutError < described_class::ConnectionError).to be(true)
    end

    it 'falls back to cause metadata for headers like it does for status_code, code and source' do
      api_error = DaytonaApiClient::ApiError.new(
        code: 429,
        response_headers: { 'Retry-After' => '60' },
        response_body: '{"statusCode":429,"message":"slow down","code":"RATE_LIMITED","source":"DAYTONA_API"}'
      )
      error = begin
        begin
          raise api_error
        rescue DaytonaApiClient::ApiError
          raise described_class::Error, 'wrapped'
        end
      rescue described_class::Error => e
        e
      end

      expect(error.headers).to eq({ 'Retry-After' => '60' })
      expect(error.status_code).to eq(429)
    end

    it 'wires every domain class to the matching HTTP-status parent' do
      pairs = {
        described_class::GitAuthFailedError => described_class::AuthenticationError,
        described_class::GitRepoNotFoundError => described_class::NotFoundError,
        described_class::GitBranchNotFoundError => described_class::NotFoundError,
        described_class::GitBranchExistsError => described_class::ConflictError,
        described_class::GitPushRejectedError => described_class::ConflictError,
        described_class::GitDirtyWorktreeError => described_class::ConflictError,
        described_class::GitMergeConflictError => described_class::ConflictError,
        described_class::GitTransportFailedError => described_class::BadGatewayError,
        described_class::GitRemoteRejectedError => described_class::UnprocessableEntityError,
        described_class::FileNotFoundError => described_class::NotFoundError,
        described_class::FileAccessDeniedError => described_class::ForbiddenError,
        described_class::LspServerNotInitializedError => described_class::ValidationError,
        described_class::ProcessExecutionTimeoutError => described_class::TimeoutError,
        described_class::ProcessNotFoundError => described_class::NotFoundError,
        described_class::SessionEndedError => described_class::GoneError,
        described_class::CommandAlreadyCompletedError => described_class::GoneError,
        described_class::A11yUnavailableError => described_class::ServiceUnavailableError,
        described_class::RecordingStillActiveError => described_class::ConflictError,
        described_class::RecordingFfmpegNotFoundError => described_class::ServiceUnavailableError
      }
      pairs.each do |child, parent|
        expect(child < parent).to be(true), "#{child} should inherit from #{parent}"
      end
    end

    it 'wires the new filesystem daemon errors to the matching HTTP-status parents' do
      pairs = {
        described_class::InvalidFilePathError => described_class::BadRequestError,
        described_class::FileReadFailedError => described_class::InternalServerError
      }

      pairs.each do |child, parent|
        expect(child < parent).to be(true), "#{child} should inherit from #{parent}"
      end
    end
  end

  describe '.error_class_for' do
    it 'routes well-known status codes to typed classes' do
      {
        400 => described_class::ValidationError,
        401 => described_class::AuthenticationError,
        403 => described_class::ForbiddenError,
        404 => described_class::NotFoundError,
        408 => described_class::TimeoutError,
        409 => described_class::ConflictError,
        410 => described_class::GoneError,
        422 => described_class::UnprocessableEntityError,
        429 => described_class::RateLimitError,
        500 => described_class::InternalServerError,
        502 => described_class::BadGatewayError,
        503 => described_class::ServiceUnavailableError,
        504 => described_class::TimeoutError
      }.each do |status, cls|
        expect(described_class.send(:error_class_for, status_code: status)).to eq(cls)
      end
    end

    it 'falls back to Error for unknown status codes' do
      expect(described_class.send(:error_class_for, status_code: 418)).to eq(described_class::Error)
      expect(described_class.send(:error_class_for, {})).to eq(described_class::Error)
    end

    it 'prefers (source, code) match over the status code' do
      details = { status_code: 404, source: 'DAYTONA_DAEMON', code: 'GIT_REPO_NOT_FOUND' }
      expect(described_class.send(:error_class_for, details)).to eq(described_class::GitRepoNotFoundError)
    end

    it 'requires source AND code to both match a registered entry' do
      details = { status_code: 404, source: 'DAYTONA_API', code: 'FILE_NOT_FOUND' }
      expect(described_class.send(:error_class_for, details)).to eq(described_class::NotFoundError)
    end

    it 'falls back to ServerError for unlisted 5xx status codes' do
      expect(described_class.send(:error_class_for, status_code: 507)).to eq(described_class::ServerError)
    end
  end

  describe '.wrap_error' do
    let(:api_error_cls) { DaytonaApiClient::ApiError }

    def api_error(status, body)
      api_error_cls.new(code: status, response_body: body, response_headers: { 'x' => 'y' })
    end

    it 'routes by HTTP status when no domain code is present' do
      err = described_class.wrap_error(api_error(404, '{"message":"missing"}'))

      expect(err).to be_a(described_class::NotFoundError)
      expect(err.status_code).to eq(404)
      expect(err.message).to eq('missing')
      expect(err.headers).to eq('x' => 'y')
    end

    it 'routes by (source, code) when both are present' do
      body = '{"message":"creds rejected","code":"GIT_AUTH_FAILED","source":"DAYTONA_DAEMON"}'
      err  = described_class.wrap_error(api_error(401, body))

      expect(err).to be_a(described_class::GitAuthFailedError)
      expect(err).to be_a(described_class::AuthenticationError) # inheritance
      expect(err).to be_a(described_class::Error)
      expect(err.code).to eq('GIT_AUTH_FAILED')
      expect(err.source).to eq('DAYTONA_DAEMON')
    end

    it 'routes the new filesystem daemon codes to their typed classes' do
      cases = [
        [400, 'INVALID_FILE_PATH', described_class::InvalidFilePathError],
        [500, 'FILE_READ_FAILED', described_class::FileReadFailedError]
      ]

      cases.each do |status, code, klass|
        body = %({"message":"typed","code":"#{code}","source":"DAYTONA_DAEMON"})
        err = described_class.wrap_error(api_error(status, body))

        expect(err).to be_a(klass)
        expect(err.code).to eq(code)
        expect(err.source).to eq('DAYTONA_DAEMON')
        expect(err.status_code).to eq(status)
      end
    end

    it 'routes GIT_TRANSPORT_FAILED to GitTransportFailedError' do
      body = '{"message":"dial tcp: lookup nonexistent.invalid: no such host","code":"GIT_TRANSPORT_FAILED","source":"DAYTONA_DAEMON"}'
      err  = described_class.wrap_error(api_error(502, body))

      expect(err).to be_a(described_class::GitTransportFailedError)
      expect(err).to be_a(described_class::BadGatewayError) # inheritance
      expect(err.code).to eq('GIT_TRANSPORT_FAILED')
    end

    it 'routes GIT_REMOTE_REJECTED to GitRemoteRejectedError' do
      body = '{"message":"command error on refs/heads/main: pre-receive hook declined","code":"GIT_REMOTE_REJECTED","source":"DAYTONA_DAEMON"}'
      err  = described_class.wrap_error(api_error(422, body))

      expect(err).to be_a(described_class::GitRemoteRejectedError)
      expect(err).to be_a(described_class::UnprocessableEntityError) # inheritance
      expect(err.code).to eq('GIT_REMOTE_REJECTED')
    end

    it 'prepends the prefix to the message' do
      err = described_class.wrap_error(api_error(409, '{"message":"exists"}'), 'Failed to add branch')

      expect(err).to be_a(described_class::ConflictError)
      expect(err.message).to eq('Failed to add branch: exists')
    end

    it 'falls back to the raw error message when no body is present' do
      err_obj = api_error_cls.new(code: 500, message: 'boom')
      err     = described_class.wrap_error(err_obj)

      expect(err).to be_a(described_class::InternalServerError)
      expect(err).to be_a(described_class::ServerError)
      expect(err.message).to include('boom')
    end

    it 'falls back to status-class for unknown codes' do
      body = '{"code":"SOMETHING_UNKNOWN","source":"DAYTONA_API","message":"nope"}'
      err  = described_class.wrap_error(api_error(404, body))

      expect(err).to be_a(described_class::NotFoundError)
      expect(err.code).to eq('SOMETHING_UNKNOWN')
      expect(err.source).to eq('DAYTONA_API')
    end
  end

  describe '.api_error_details' do
    it 'returns empty hash for non-ApiError instances' do
      expect(described_class.api_error_details(StandardError.new('plain'))).to eq({})
    end

    it 'extracts status, code and source from a DaytonaApiClient::ApiError' do
      err = DaytonaApiClient::ApiError.new(
        code: 401,
        response_body: '{"code":"GIT_AUTH_FAILED","source":"DAYTONA_DAEMON"}'
      )
      expect(described_class.api_error_details(err)).to include(
        status_code: 401,
        code: 'GIT_AUTH_FAILED',
        source: 'DAYTONA_DAEMON'
      )
    end

    it 'tolerates non-JSON response bodies' do
      err = DaytonaApiClient::ApiError.new(code: 502, response_body: 'not-json')
      expect(described_class.api_error_details(err)).to include(status_code: 502, code: nil, source: nil)
    end
  end
end
