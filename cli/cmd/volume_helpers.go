// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"strconv"
	"strings"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

type volumeBucketCredentialsView struct {
	AccessKeySecret string `json:"access_key_secret"`
	SecretKeySecret string `json:"secret_key_secret"`
}

type volumeBucketOptionsView struct {
	Credentials *volumeBucketCredentialsView `json:"credentials,omitempty"`
	Endpoint    *string                      `json:"endpoint,omitempty"`
	Name        string                       `json:"name"`
	Origin      *string                      `json:"origin,omitempty"`
	Prefix      *string                      `json:"prefix,omitempty"`
	Region      *string                      `json:"region,omitempty"`
}

type volumeOptionsView struct {
	Bucket *volumeBucketOptionsView `json:"bucket,omitempty"`
}

func formatSizeGib(value *int32) string {
	if value == nil {
		return ""
	}
	return strconv.Itoa(int(*value))
}

func volumeKV(volume gen.Volume) [][2]string {
	rows := [][2]string{{"id", volume.Id}, {"name", volume.Name}, {"type", string(volume.Type)}, {"status", string(volume.Status)}, {"size_gib", formatSizeGib(volume.SizeGib)}, {"updated_at", volume.UpdatedAt.Format(time.RFC3339)}}
	bucket := volumeBucketOptions(volume)
	if bucket == nil {
		return rows
	}
	return append(rows,
		[2]string{"bucket_name", bucket.Name},
		[2]string{"bucket_origin", volumeBucketOrigin(bucket)},
		[2]string{"bucket_endpoint", formatPtr(volumeBucketEndpoint(bucket))},
		[2]string{"bucket_region", formatPtr(volumeBucketRegion(bucket))},
		[2]string{"bucket_prefix", formatPtr(volumeBucketPrefix(bucket))},
		[2]string{"bucket_access_key_secret", volumeBucketAccessKeySecret(bucket)},
		[2]string{"bucket_secret_key_secret", volumeBucketSecretKeySecret(bucket)},
	)
}

// validateVolumeCreateRequest reports invalid bucket options before sending a request.
func validateVolumeCreateRequest(req gen.CreateVolumeRequest) error {
	if req.SizeGib != nil && *req.SizeGib <= 0 {
		return cerr.Usage("--size-gib must be greater than zero")
	}
	if req.Type != nil && !req.Type.Valid() {
		return cerr.Usage("the volume type must be disk or bucket (got %q); omit it to let the server choose", string(*req.Type))
	}
	if volumeType(req) == "bucket" && req.SizeGib != nil {
		return cerr.Usage("--size-gib applies to disk volumes only; a bucket volume has no scratch size")
	}
	if req.Bucket == nil {
		return nil
	}
	if volumeType(req) != "bucket" {
		return cerr.Usage("bucket options require --type bucket")
	}
	if strings.TrimSpace(req.Bucket.Name) == "" {
		return cerr.Usage("bucket options require --bucket or bucket.name in --file")
	}
	if req.Bucket.Origin != nil && *req.Bucket.Origin != "external" {
		return cerr.Usage("bucket.origin must be \"external\" (a platform-owned bucket is requested by omitting the bucket object)")
	}
	// Customer buckets require both project secret names.
	if req.Bucket.Credentials == nil {
		return cerr.Usage("a customer bucket needs --access-key-secret and --secret-key-secret (project secret names)")
	}
	hasAccessKey := strings.TrimSpace(req.Bucket.Credentials.AccessKeySecret) != ""
	hasSecretKey := strings.TrimSpace(req.Bucket.Credentials.SecretKeySecret) != ""
	if !hasAccessKey || !hasSecretKey {
		return cerr.Usage("--access-key-secret and --secret-key-secret are required together")
	}
	return nil
}

// volumeType is the type the request names, "" when it leaves the choice to the server.
func volumeType(req gen.CreateVolumeRequest) string {
	if req.Type == nil {
		return ""
	}
	return strings.TrimSpace(string(*req.Type))
}

func volumeBucketOptions(volume gen.Volume) *volumeBucketOptionsView {
	if volume.Options == nil {
		return nil
	}
	encoded, err := json.Marshal(volume.Options)
	if err != nil {
		return nil
	}
	var options volumeOptionsView
	if err := json.Unmarshal(encoded, &options); err != nil {
		return nil
	}
	return options.Bucket
}

func volumeBucketName(bucket *volumeBucketOptionsView) string {
	if bucket == nil {
		return ""
	}
	return bucket.Name
}

func volumeBucketOrigin(bucket *volumeBucketOptionsView) string {
	if bucket == nil || bucket.Origin == nil {
		return ""
	}
	return string(*bucket.Origin)
}

func volumeBucketEndpoint(bucket *volumeBucketOptionsView) *string {
	if bucket == nil {
		return nil
	}
	return bucket.Endpoint
}

func volumeBucketRegion(bucket *volumeBucketOptionsView) *string {
	if bucket == nil {
		return nil
	}
	return bucket.Region
}

func volumeBucketPrefix(bucket *volumeBucketOptionsView) *string {
	if bucket == nil {
		return nil
	}
	return bucket.Prefix
}

func volumeBucketAccessKeySecret(bucket *volumeBucketOptionsView) string {
	if bucket == nil || bucket.Credentials == nil {
		return ""
	}
	return bucket.Credentials.AccessKeySecret
}

func volumeBucketSecretKeySecret(bucket *volumeBucketOptionsView) string {
	if bucket == nil || bucket.Credentials == nil {
		return ""
	}
	return bucket.Credentials.SecretKeySecret
}
