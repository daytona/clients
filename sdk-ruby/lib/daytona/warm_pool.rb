# frozen_string_literal: true

module Daytona
  # A warm pool of ready-to-use Sandboxes for a snapshot. `current_size` versus
  # `pool` is the pool's status: `current_size` is the number of ready warm
  # sandboxes, `pool` is the desired number. `error_reason` is set when the
  # pool cannot be filled.
  class WarmPool
    # @return [String]
    attr_reader :id

    # @return [String]
    attr_reader :organization_id

    # @return [String]
    attr_reader :snapshot

    # @return [String]
    attr_reader :target

    # @return [Integer]
    attr_reader :pool

    # @return [Integer]
    attr_reader :current_size

    # @return [Integer]
    attr_reader :cpu

    # @return [Integer]
    attr_reader :mem

    # @return [Integer]
    attr_reader :disk

    # @return [String]
    attr_reader :os_user

    # @return [Hash{String => String}]
    attr_reader :env

    # @return [String, nil]
    attr_reader :error_reason

    # @return [String]
    attr_reader :created_at

    # @return [String]
    attr_reader :updated_at

    # Initialize warm pool from DTO
    #
    # @param warm_pool_dto [DaytonaApiClient::WarmPool]
    def initialize(warm_pool_dto)
      @id = warm_pool_dto.id
      @organization_id = warm_pool_dto.organization_id
      @snapshot = warm_pool_dto.snapshot
      @target = warm_pool_dto.target
      # The generated client deserializes OpenAPI numbers as Float; these are all integral.
      @pool = warm_pool_dto.pool.to_i
      @current_size = warm_pool_dto.current_size.to_i
      @cpu = warm_pool_dto.cpu.to_i
      @mem = warm_pool_dto.mem.to_i
      @disk = warm_pool_dto.disk.to_i
      @os_user = warm_pool_dto.os_user
      @env = warm_pool_dto.env
      @error_reason = warm_pool_dto.error_reason
      @created_at = warm_pool_dto.created_at
      @updated_at = warm_pool_dto.updated_at
    end
  end
end
