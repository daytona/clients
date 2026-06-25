# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  class SecretService
    include Instrumentation

    # Service for managing organization-scoped Daytona Secrets. Can be used to list, get, create,
    # update and delete Secrets.
    #
    # A Secret stores a plaintext +value+ that is never returned by the API. When a Secret is
    # referenced while creating a Sandbox, the corresponding env var holds an opaque +placeholder+
    # that is resolved to the real value only for the Secret's allowed +hosts+.
    #
    # @param secret_api [DaytonaApiClient::SecretApi]
    # @param otel_state [Daytona::OtelState, nil]
    def initialize(secret_api, otel_state: nil)
      @secret_api = secret_api
      @otel_state = otel_state
    end

    # Create a new Secret.
    #
    # @param name [String] Name of the Secret. Must match +^[a-zA-Z_][a-zA-Z0-9_-]*$+ and be unique
    #   within the organization (a duplicate name raises a 409 error).
    # @param value [String] Plaintext value of the Secret. Write-only; never returned by the API.
    # @param description [String, nil] Optional description of the Secret.
    # @param hosts [Array<String>, nil] Allowed hosts this Secret may be sent to. Accepts exact
    #   hostnames and +*.+ wildcards (no ports).
    # @return [Daytona::Secret]
    def create(name, value, description: nil, hosts: nil)
      Secret.new(secret_api.create_secret(
                   DaytonaApiClient::CreateSecret.new(name:, value:, description:, hosts:)
                 ))
    end

    # Delete a Secret.
    #
    # @param secret_id [String]
    # @return [void]
    # @raise [DaytonaApiClient::ApiError] If no Secret with the given ID exists (404).
    def delete(secret_id) = secret_api.delete_secret(secret_id)

    # Get a Secret by ID.
    #
    # @param secret_id [String]
    # @return [Daytona::Secret]
    # @raise [DaytonaApiClient::ApiError] If no Secret with the given ID exists (404).
    def get(secret_id) = Secret.new(secret_api.get_secret(secret_id))

    # List all Secrets.
    #
    # @return [Array<Daytona::Secret>]
    def list
      secret_api.list_secrets.map { |secret| Secret.new(secret) }
    end

    # Update a Secret.
    #
    # @param secret_id [String]
    # @param value [String, nil] New plaintext value. Write-only; never returned by the API.
    # @param description [String, nil] New description of the Secret.
    # @param hosts [Array<String>, nil] Allowed hosts this Secret may be sent to. Accepts exact
    #   hostnames and +*.+ wildcards (no ports).
    # @return [Daytona::Secret]
    # @raise [DaytonaApiClient::ApiError] If no Secret with the given ID exists (404).
    def update(secret_id, value: nil, description: nil, hosts: nil)
      Secret.new(secret_api.update_secret(
                   secret_id,
                   DaytonaApiClient::UpdateSecret.new(value:, description:, hosts:)
                 ))
    end

    instrument :create, :delete, :get, :list, :update, component: 'SecretService'

    private

    # @return [DaytonaApiClient::SecretApi]
    attr_reader :secret_api

    # @return [Daytona::OtelState, nil]
    attr_reader :otel_state
  end
end
