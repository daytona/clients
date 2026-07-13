// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
/**
 * Result of an explicit blockmount volume pull into a running Sandbox.
 */
public class VolumePullResult {
    @JsonProperty("volumeId")
    private String volumeId;
    @JsonProperty("manifestId")
    private String manifestId;
    @JsonProperty("upToDate")
    private boolean upToDate;
    @JsonProperty("filesWritten")
    private int filesWritten;
    @JsonProperty("deleted")
    private int deleted;
    @JsonProperty("skippedLocalNewer")
    private int skippedLocalNewer;
    @JsonProperty("bytesFetched")
    private long bytesFetched;

    /**
     * Returns the identifier of the volume that was pulled.
     *
     * @return volume identifier
     */
    public String getVolumeId() {
        return volumeId;
    }

    /**
     * Sets the identifier of the volume that was pulled.
     *
     * @param volumeId volume identifier
     */
    public void setVolumeId(String volumeId) {
        this.volumeId = volumeId;
    }

    /**
     * Returns the merged manifest the Sandbox's scratch was advanced to.
     *
     * @return manifest identifier, or {@code null} when the volume had no commits
     */
    public String getManifestId() {
        return manifestId;
    }

    /**
     * Sets the merged manifest identifier.
     *
     * @param manifestId manifest identifier
     */
    public void setManifestId(String manifestId) {
        this.manifestId = manifestId;
    }

    /**
     * Returns whether the Sandbox already reflected the latest merged state.
     *
     * @return {@code true} when nothing had to be pulled
     */
    public boolean isUpToDate() {
        return upToDate;
    }

    /**
     * Sets whether the Sandbox already reflected the latest merged state.
     *
     * @param upToDate up-to-date flag
     */
    public void setUpToDate(boolean upToDate) {
        this.upToDate = upToDate;
    }

    /**
     * Returns the number of files and symlinks written into the Sandbox by the pull.
     *
     * @return written file count
     */
    public int getFilesWritten() {
        return filesWritten;
    }

    /**
     * Sets the number of files and symlinks written into the Sandbox by the pull.
     *
     * @param filesWritten written file count
     */
    public void setFilesWritten(int filesWritten) {
        this.filesWritten = filesWritten;
    }

    /**
     * Returns the number of paths removed because they were deleted in the merged state.
     *
     * @return deleted path count
     */
    public int getDeleted() {
        return deleted;
    }

    /**
     * Sets the number of paths removed because they were deleted in the merged state.
     *
     * @param deleted deleted path count
     */
    public void setDeleted(int deleted) {
        this.deleted = deleted;
    }

    /**
     * Returns the number of paths left untouched because the Sandbox has a strictly newer local
     * modification (the next commit's last-change-wins merge resolves them).
     *
     * @return skipped path count
     */
    public int getSkippedLocalNewer() {
        return skippedLocalNewer;
    }

    /**
     * Sets the number of paths skipped as locally newer.
     *
     * @param skippedLocalNewer skipped path count
     */
    public void setSkippedLocalNewer(int skippedLocalNewer) {
        this.skippedLocalNewer = skippedLocalNewer;
    }

    /**
     * Returns the number of content bytes downloaded from the store.
     *
     * @return downloaded byte count
     */
    public long getBytesFetched() {
        return bytesFetched;
    }

    /**
     * Sets the number of content bytes downloaded from the store.
     *
     * @param bytesFetched downloaded byte count
     */
    public void setBytesFetched(long bytesFetched) {
        this.bytesFetched = bytesFetched;
    }
}
