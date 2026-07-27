# frozen_string_literal: true

module Daytona
  class WarmPoolService
    include Instrumentation

    # Service for managing Daytona Warm Pools. Can be used to list, create, update and delete Warm Pools.
    #
    # @param warm_pools_api [DaytonaApiClient::WarmPoolsApi]
    # @param otel_state [Daytona::OtelState, nil]
    def initialize(warm_pools_api, otel_state: nil)
      @warm_pools_api = warm_pools_api
      @otel_state = otel_state
    end

    # Create a new Warm Pool.
    #
    # @param snapshot [String] The snapshot (ID or name) to keep warm sandboxes for
    # @param pool [Integer] Number of warm sandboxes to keep ready
    # @param target [String, nil] Target region; defaults to the organization default region
    # @return [Daytona::WarmPool]
    def create(snapshot, pool, target: nil)
      WarmPool.new(warm_pools_api.create_warm_pool(DaytonaApiClient::CreateWarmPool.new(snapshot:, pool:, target:)))
    end

    # Delete a Warm Pool.
    #
    # @param warm_pool_id [String]
    # @return [void]
    def delete(warm_pool_id) = warm_pools_api.delete_warm_pool(warm_pool_id)

    # List all Warm Pools.
    #
    # @return [Array<Daytona::WarmPool>]
    def list
      warm_pools_api.list_warm_pools.map { |warm_pool| WarmPool.new(warm_pool) }
    end

    # Update the desired size of a Warm Pool.
    #
    # @param warm_pool_id [String]
    # @param pool [Integer] New desired number of warm sandboxes (0 drains the pool)
    # @return [Daytona::WarmPool]
    def update(warm_pool_id, pool)
      WarmPool.new(warm_pools_api.update_warm_pool(warm_pool_id, DaytonaApiClient::UpdateWarmPool.new(pool:)))
    end

    instrument :create, :delete, :list, :update, component: 'WarmPoolService'

    private

    # @return [DaytonaApiClient::WarmPoolsApi]
    attr_reader :warm_pools_api

    # @return [Daytona::OtelState, nil]
    attr_reader :otel_state
  end
end
