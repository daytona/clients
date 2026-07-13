# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

module Daytona
  class VolumeService
    include Instrumentation

    # Service for managing Daytona Volumes. Can be used to list, get, create and delete Volumes.
    #
    # @param volumes_api [DaytonaApiClient::VolumesApi]
    # @param otel_state [Daytona::OtelState, nil]
    def initialize(volumes_api, otel_state: nil)
      @volumes_api = volumes_api
      @otel_state = otel_state
    end

    # Create new Volume.
    #
    # @param name [String]
    # @param type [String, nil] The volume type. Defaults to legacy.
    # @param region [String, nil] The region to create the Volume in. For blockmount volumes it selects
    #   the region-local store the Volume's data lives in — a performance/placement knob; Sandboxes in any
    #   region can attach it. Optional for blockmount, defaulting to the organization's default region when
    #   omitted. Selects the deployment region for hotmount volumes.
    # @return [Daytona::Volume]
    def create(name, type: nil, region: nil)
      Volume.new(volumes_api.create_volume(DaytonaApiClient::CreateVolume.new(name:, type:, region:)))
    end

    # Delete a Volume.
    #
    # @param volume [Daytona::Volume]
    # @return [void]
    def delete(volume) = volumes_api.delete_volume(volume.id)

    # Get a Volume by name.
    #
    # @param name [String]
    # @param create [Boolean]
    # @param type [String, nil] The volume type to create (only used if create is true).
    # @param region [String, nil] The region to create the Volume in. For blockmount volumes it selects
    #   the region-local store the Volume's data lives in — a performance/placement knob; Sandboxes in any
    #   region can attach it. Optional for blockmount, defaulting to the organization's default region when
    #   omitted. Selects the deployment region for hotmount volumes.
    # @return [Daytona::Volume]
    def get(name, create: false, type: nil, region: nil)
      Volume.new(volumes_api.get_volume_by_name(name))
    rescue DaytonaApiClient::ApiError => e
      raise unless create && e.code == 404 && e.message.include?("Volume with name #{name} not found")

      create(name, type:, region:)
    end

    # Create a short-lived mount token for a hotmount Volume.
    #
    # The token, together with the returned region gateway/binaries endpoints, is used to
    # bootstrap the hotmount agent (inside a Sandbox or on customer infrastructure) and mount
    # the Volume on the fly. Only hotmount volumes support this.
    #
    # @param volume [Daytona::Volume]
    # @return [DaytonaApiClient::VolumeMountTokenDto]
    def get_mount_token(volume) = volumes_api.create_volume_mount_token(volume.id)

    # List the hotmount regions available for volume creation.
    #
    # @return [Array<DaytonaApiClient::HotmountRegion>]
    def list_hotmount_regions = volumes_api.list_hotmount_regions

    # List the regions where blockmount Volumes can be created. A blockmount Volume's data lives in
    # the region it is created in (a performance/placement knob — Sandboxes in any region can attach
    # it, colocation is just faster).
    #
    # @return [Array<DaytonaApiClient::Region>]
    def list_blockmount_regions = volumes_api.list_blockmount_regions

    # List all Volumes.
    #
    # @return [Array<Daytona::Volume>]
    def list
      volumes_api.list_volumes.map { |volume| Volume.new(volume) }
    end

    instrument :create, :delete, :get, :get_mount_token, :list_hotmount_regions, :list_blockmount_regions, :list,
               component: 'VolumeService'

    private

    # @return [DaytonaApiClient::VolumesApi]
    attr_reader :volumes_api

    # @return [Daytona::OtelState, nil]
    attr_reader :otel_state
  end
end
