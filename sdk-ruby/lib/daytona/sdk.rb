# frozen_string_literal: true

# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

require 'json'
require 'logger'

require 'daytona_api_client'
require 'daytona_toolbox_api_client'
require 'daytona_analytics_api_client'
require 'toml'
require 'websocket-client-simple'

require_relative 'sdk/version'
require_relative 'sdk/errors'
require_relative 'sdk/deprecated'
require_relative 'sdk/file_download_patch'
require_relative 'config'
require_relative 'otel'
require_relative 'common/charts'
require_relative 'common/code_interpreter'
require_relative 'common/code_language'
require_relative 'common/daytona'
require_relative 'common/file_system'
require_relative 'common/image'
require_relative 'common/git'
require_relative 'common/process'
require_relative 'common/pty'
require_relative 'common/resources'
require_relative 'common/response'
require_relative 'common/snapshot'
require_relative 'code_interpreter'
require_relative 'computer_use'
require_relative 'common/socketio_client'
require_relative 'common/websocket_dialer'
require_relative 'common/event_dispatcher'
require_relative 'common/event_subscription_manager'
require_relative 'daytona'
require_relative 'file_system'
require_relative 'git'
require_relative 'lsp_server'
require_relative 'object_storage'
require_relative 'sandbox'
require_relative 'secret'
require_relative 'secret_service'
require_relative 'snapshot_service'
require_relative 'util'
require_relative 'volume'
require_relative 'volume_service'
require_relative 'warm_pool'
require_relative 'warm_pool_service'
require_relative 'process'

module Daytona
  module Sdk
    # The error hierarchy and translation helpers live in `sdk/errors.rb`.
    # This file just provides cross-cutting bits that need to be loaded
    # alongside them.

    def self.logger = @logger ||= Logger.new($stdout, level: Logger::INFO)
  end

  module V1
    def self.const_missing(name)
      const = ::Daytona.const_get(name, false)
      const_set(name, const)
    end

    DaytonaV1 = Daytona
    SandboxV1 = Sandbox
    ImageV1 = Image
    ConfigV1 = Config
    ResourcesV1 = Resources
    ListSandboxesQueryV1 = ListSandboxesQuery

    ErrorV1 = Error
    BadRequestErrorV1 = BadRequestError
    AuthenticationErrorV1 = AuthenticationError
    ForbiddenErrorV1 = ForbiddenError
    AuthorizationErrorV1 = AuthorizationError
    NotFoundErrorV1 = NotFoundError
    TimeoutErrorV1 = TimeoutError
    ConflictErrorV1 = ConflictError
    GoneErrorV1 = GoneError
    UnprocessableEntityErrorV1 = UnprocessableEntityError
    RateLimitErrorV1 = RateLimitError
    InternalServerErrorV1 = InternalServerError
    ServiceUnavailableErrorV1 = ServiceUnavailableError
    ConnectionErrorV1 = ConnectionError
    ValidationErrorV1 = ValidationError
  end
end
