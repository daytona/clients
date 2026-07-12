// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.VolumesApi;
import io.daytona.api.client.model.CreateVolume;
import io.daytona.api.client.model.HotmountRegion;
import io.daytona.api.client.model.Region;
import io.daytona.api.client.model.VolumeMountTokenDto;
import io.daytona.api.client.model.VolumeType;
import io.daytona.sdk.model.Volume;

import java.util.List;
import java.util.ArrayList;

/**
 * Service for managing Daytona Volumes.
 *
 * <p>Volumes provide persistent shared storage that can be mounted into Sandboxes.
 */
public class VolumeService {
    private final VolumesApi volumesApi;

    VolumeService(VolumesApi volumesApi) {
        this.volumesApi = volumesApi;
    }

    /**
     * Creates a new legacy volume.
     *
     * @param name volume name
     * @return created {@link Volume}
     * @throws io.daytona.sdk.exception.DaytonaException if creation fails
     */
    public Volume create(String name) {
        return create(name, null, null);
    }

    /**
     * Creates a new volume of the given type.
     *
     * @param name volume name
     * @param type volume type, or null to default to legacy
     * @return created {@link Volume}
     * @throws io.daytona.sdk.exception.DaytonaException if creation fails
     */
    public Volume create(String name, VolumeType type) {
        return create(name, type, (String) null);
    }

    /**
     * Creates a new volume of the given type, with a region.
     *
     * @param name volume name
     * @param type volume type, or null to default to legacy
     * @param region region to create the volume in; for blockmount volumes it selects the region-local
     *     store the volume's data lives in — a performance/placement knob, sandboxes in any region can
     *     attach it (optional for blockmount, defaults to the organization's default region when omitted),
     *     selects the deployment region for hotmount volumes, not allowed for legacy
     * @return created {@link Volume}
     * @throws io.daytona.sdk.exception.DaytonaException if creation fails
     */
    public Volume create(String name, VolumeType type, String region) {
        io.daytona.api.client.model.VolumeDto volumeDto = ExceptionMapper.callMain(
                () -> volumesApi.createVolume(
                        new CreateVolume().name(name).type(type).region(region), null)
        );
        return toVolume(volumeDto);
    }

    /**
     * Creates a short-lived mount token for a hotmount volume.
     *
     * <p>The token, together with the returned region gateway/binaries endpoints, is used to
     * bootstrap the hotmount agent (inside a Sandbox or on customer infrastructure) and mount the
     * volume on the fly. Only hotmount volumes support this.
     *
     * @param volumeId the hotmount volume identifier
     * @return the mount token, region endpoints, and expiration
     * @throws io.daytona.sdk.exception.DaytonaException if the request fails
     */
    public VolumeMountTokenDto getMountToken(String volumeId) {
        return ExceptionMapper.callMain(() -> volumesApi.createVolumeMountToken(volumeId, null, null));
    }

    /**
     * Lists the hotmount regions available for volume creation.
     *
     * @return list of active hotmount regions
     * @throws io.daytona.sdk.exception.DaytonaException if the request fails
     */
    public List<HotmountRegion> listHotmountRegions() {
        List<HotmountRegion> regions = ExceptionMapper.callMain(() -> volumesApi.listHotmountRegions(null));
        return regions != null ? regions : new ArrayList<HotmountRegion>();
    }

    /**
     * Lists the regions where blockmount volumes can be created.
     *
     * <p>A blockmount volume's data lives in the region it is created in (a performance/placement knob —
     * sandboxes in any region can attach it, colocation is just faster). Only regions a superadmin has
     * enabled for blockmount are returned.
     *
     * @return list of regions that support blockmount volumes
     * @throws io.daytona.sdk.exception.DaytonaException if the request fails
     */
    public List<Region> listBlockmountRegions() {
        List<Region> regions = ExceptionMapper.callMain(() -> volumesApi.listBlockmountRegions(null));
        return regions != null ? regions : new ArrayList<Region>();
    }

    /**
     * Lists all accessible volumes.
     *
     * @return list of available volumes
     * @throws io.daytona.sdk.exception.DaytonaException if the API request fails
     */
    public List<Volume> list() {
        List<io.daytona.api.client.model.VolumeDto> volumes = ExceptionMapper.callMain(() -> volumesApi.listVolumes(null, null));
        List<Volume> result = new ArrayList<Volume>();
        if (volumes != null) {
            for (io.daytona.api.client.model.VolumeDto volume : volumes) {
                result.add(toVolume(volume));
            }
        }
        return result;
    }

    /**
     * Retrieves a volume by name.
     *
     * @param name volume name
     * @return matching {@link Volume}
     * @throws io.daytona.sdk.exception.DaytonaException if no volume is found or request fails
     */
    public Volume getByName(String name) {
        io.daytona.api.client.model.VolumeDto volumeDto = ExceptionMapper.callMain(() -> volumesApi.getVolumeByName(name, null));
        return toVolume(volumeDto);
    }

    /**
     * Deletes a volume by ID.
     *
     * @param id volume identifier
     * @throws io.daytona.sdk.exception.DaytonaException if deletion fails
     */
    public void delete(String id) {
        ExceptionMapper.runMain(() -> volumesApi.deleteVolume(id, null));
    }

    private Volume toVolume(io.daytona.api.client.model.VolumeDto source) {
        Volume volume = new Volume();
        if (source != null) {
            volume.setId(source.getId());
            volume.setName(source.getName());
            volume.setType(source.getType() == null ? null : source.getType().getValue());
            volume.setSizeInGb(source.getSizeInGb());
            volume.setRegion(source.getRegion());
            volume.setShared(source.getShared());
            volume.setState(source.getState() == null ? null : source.getState().getValue());
        }
        return volume;
    }
}
