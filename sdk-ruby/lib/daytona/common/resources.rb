# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  class Resources
    # @return [Integer, nil] Number of CPU cores to allocate
    attr_reader :cpu

    # @return [Integer, nil] Amount of memory in GiB to allocate
    attr_reader :memory

    # @return [Integer, nil] Amount of disk space in GiB to allocate
    attr_reader :disk

    # @return [Integer, nil] Number of GPUs to allocate
    attr_reader :gpu

    # @return [String, Array<String>, nil] Preferred GPU type for the Sandbox
    attr_reader :gpu_type

    # @return [Array<Integer>, nil] CPU cores for each node in rank order
    attr_reader :cpu_per_node

    # @return [Array<Integer>, nil] Memory in GiB for each node in rank order
    attr_reader :memory_per_node

    # @return [Array<Integer>, nil] Disk space in GiB for each node in rank order
    attr_reader :disk_per_node

    # @param cpu [Integer, nil] Number of CPU cores to allocate
    # @param memory [Integer, nil] Amount of memory in GiB to allocate
    # @param disk [Integer, nil] Amount of disk space in GiB to allocate
    # @param gpu [Integer, nil] Number of GPUs to allocate
    # @param gpu_type [String, Array<String>, nil] Preferred GPU type for the Sandbox
    # @param cpu_per_node [Array<Integer>, nil] CPU cores for each node in rank order
    # @param memory_per_node [Array<Integer>, nil] Memory in GiB for each node in rank order
    # @param disk_per_node [Array<Integer>, nil] Disk space in GiB for each node in rank order
    def initialize(cpu: nil, memory: nil, disk: nil, gpu: nil, gpu_type: nil,
                   cpu_per_node: nil, memory_per_node: nil, disk_per_node: nil)
      [[cpu, cpu_per_node], [memory, memory_per_node], [disk, disk_per_node]].each do |scalar, values|
        next if values.nil?
        raise Sdk::Error, 'Specify either the scalar or its per-node values' unless scalar.nil?
        next if values.is_a?(Array) && !values.empty? && values.all? { |item| item.is_a?(Integer) && item.positive? }

        raise Sdk::Error, 'Per-node resources must be non-empty arrays of positive integers'
      end
      @cpu_per_node = cpu_per_node
      @memory_per_node = memory_per_node
      @disk_per_node = disk_per_node
      @cpu = cpu
      @memory = memory
      @disk = disk
      @gpu = gpu
      @gpu_type = gpu_type
    end

    # @api private
    def validate_scalar!
      raise Sdk::Error, 'Per-node resources are only supported for sandbox creation' if [cpu_per_node, memory_per_node,
                                                                                         disk_per_node].any?
    end

    # @return [Hash] Hash representation of the resources
    def to_h = { cpu:, memory:, disk:, gpu:, gpu_type:, cpu_per_node:, memory_per_node:, disk_per_node: }.compact
  end
end
