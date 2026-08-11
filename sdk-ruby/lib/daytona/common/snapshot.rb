# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  class CreateSnapshotParams
    # @return [String] Name of the snapshot
    attr_reader :name

    # @return [String, Daytona::Image] Image of the snapshot. If a string is provided,
    #   it should be available on some registry. If an Image instance is provided,
    #   it will be used to create a new image in Daytona.
    attr_reader :image

    # @return [Daytona::Resources, nil] Resources of the snapshot
    attr_reader :resources

    # @return [Array<String>, nil] Entrypoint of the snapshot
    attr_reader :entrypoint

    # @return [String, nil] ID of the region where the snapshot will be available.
    #   Defaults to organization default region if not specified. Mutually exclusive
    #   with region_ids.
    attr_reader :region_id

    # @return [Array<String>, nil] IDs of the regions where the snapshot will be available.
    #   Mutually exclusive with region_id. When set, the client's default region (target) is
    #   not applied. Duplicates are ignored and the order carries no meaning - the server
    #   selects the region that performs the initial build or pull. Requesting more than one
    #   region requires the multi-region snapshots feature to be enabled for the organization,
    #   is not supported for GPU snapshots, and is only possible between regions that share
    #   an internal registry.
    attr_reader :region_ids

    # @return [DaytonaApiClient::SandboxClass, nil] Target sandbox class.
    attr_reader :sandbox_class

    # @param name [String] Name of the snapshot
    # @param image [String, Daytona::Image] Image of the snapshot
    # @param resources [Daytona::Resources, nil] Resources of the snapshot
    # @param entrypoint [Array<String>, nil] Entrypoint of the snapshot
    # @param region_id [String, nil] ID of the region where the snapshot will be available
    # @param region_ids [Array<String>, nil] IDs of the regions where the snapshot will be available
    # @param sandbox_class [DaytonaApiClient::SandboxClass, nil] Target sandbox class
    def initialize(name:, image:, resources: nil, entrypoint: nil, region_id: nil, region_ids: nil,
                   sandbox_class: nil)
      @name = name
      @image = image
      @resources = resources
      @entrypoint = entrypoint
      @region_id = region_id
      @region_ids = region_ids
      @sandbox_class = sandbox_class
    end
  end

  class Snapshot
    # Matches RFC 4122 UUIDs (versions 1-5) and the nil UUID - the same set the
    # Daytona API recognizes as snapshot IDs. Anything else is treated as a name.
    SNAPSHOT_ID_PATTERN = Regexp.new(
      '\A(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}' \
      '|00000000-0000-0000-0000-000000000000)\z',
      Regexp::IGNORECASE
    ).freeze

    # @return [String] Unique identifier for the Snapshot
    attr_reader :id

    # @return [String, nil] Organization ID of the Snapshot
    attr_reader :organization_id

    # @return [Boolean, nil] Whether the Snapshot is general
    attr_reader :general

    # @return [String] Name of the Snapshot
    attr_reader :name

    # @return [String] Name of the Image of the Snapshot
    attr_reader :image_name

    # @return [String] State of the Snapshot
    attr_reader :state

    # @return [Float, nil] Size of the Snapshot
    attr_reader :size

    # @return [Array<String>, nil] Entrypoint of the Snapshot
    attr_reader :entrypoint

    # @return [Float] CPU of the Snapshot
    attr_reader :cpu

    # @return [Float] GPU of the Snapshot
    attr_reader :gpu

    # @return [Float] Memory of the Snapshot in GiB
    attr_reader :mem

    # @return [Float] Disk of the Snapshot in GiB
    attr_reader :disk

    # @return [String, nil] Error reason of the Snapshot
    attr_reader :error_reason

    # @return [String] Timestamp when the Snapshot was created
    attr_reader :created_at

    # @return [String] Timestamp when the Snapshot was last updated
    attr_reader :updated_at

    # @return [String, nil] Timestamp when the Snapshot was last used
    attr_reader :last_used_at

    # @return [DaytonaApiClient::BuildInfo, nil] Build information for the snapshot
    attr_reader :build_info

    # @return [String, nil] ID of the sandbox the Snapshot was created from
    attr_reader :source_sandbox_id

    # @return [Array<String>, nil] IDs of the regions where the Snapshot is available
    attr_reader :region_ids

    # @param snapshot_dto [DaytonaApiClient::SnapshotDto] The snapshot DTO from the API
    def initialize(snapshot_dto) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
      @id = snapshot_dto.id
      @organization_id = snapshot_dto.organization_id
      @general = snapshot_dto.general
      @name = snapshot_dto.name
      @image_name = snapshot_dto.image_name
      @state = snapshot_dto.state
      @size = snapshot_dto.size
      @entrypoint = snapshot_dto.entrypoint
      @cpu = snapshot_dto.cpu
      @gpu = snapshot_dto.gpu
      @mem = snapshot_dto.mem
      @disk = snapshot_dto.disk
      @error_reason = snapshot_dto.error_reason
      @created_at = snapshot_dto.created_at
      @updated_at = snapshot_dto.updated_at
      @last_used_at = snapshot_dto.last_used_at
      @build_info = snapshot_dto.build_info
      @source_sandbox_id = snapshot_dto.source_sandbox_id
      @region_ids = snapshot_dto.region_ids
    end

    # Creates a Snapshot instance from a SnapshotDto
    #
    # @param dto [DaytonaApiClient::SnapshotDto] The snapshot DTO from the API
    # @return [Daytona::Snapshot] The snapshot instance
    def self.from_dto(dto) = new(dto)

    # Whether the given value looks like a Snapshot ID (a UUID) rather than a name.
    #
    # @param value [Object] value to test
    # @return [Boolean] true when value is a UUID-shaped String
    def self.id?(value) = value.is_a?(String) && SNAPSHOT_ID_PATTERN.match?(value)
  end
end
