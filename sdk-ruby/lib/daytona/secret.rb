# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  class Secret
    # @return [String]
    attr_reader :id

    # @return [String]
    attr_reader :name

    # @return [String, nil]
    attr_reader :description

    # @return [String] Opaque placeholder token injected as the env var value in Sandboxes. The
    #   placeholder is resolved to the real plaintext value only for the secret's allowed hosts.
    attr_reader :placeholder

    # @return [Array<String>] Allowed hosts this secret may be sent to. Accepts exact hostnames
    #   and +*.+ wildcards (no ports).
    attr_reader :hosts

    # @return [String]
    attr_reader :created_at

    # @return [String]
    attr_reader :updated_at

    # Initialize secret from DTO
    #
    # The plaintext value is write-only and is never returned by the API, so it is not exposed here.
    #
    # @param secret_dto [DaytonaApiClient::Secret]
    def initialize(secret_dto)
      @id = secret_dto.id
      @name = secret_dto.name
      @description = secret_dto.description
      @placeholder = secret_dto.placeholder
      @hosts = secret_dto.hosts
      @created_at = secret_dto.created_at
      @updated_at = secret_dto.updated_at
    end
  end
end
