// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"archive/tar"
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/aws/retry"
	awshttp "github.com/aws/aws-sdk-go-v2/aws/transport/http"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go/middleware"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// fakeS3 is a minimal S3 endpoint that records how an object was written, so the
// tests can tell a streamed multipart upload apart from a single buffered PutObject.
type fakeS3 struct {
	mu          sync.Mutex
	parts       map[int][]byte
	singlePut   []byte
	contentType string
}

func newFakeS3(t *testing.T) (*fakeS3, *httptest.Server) {
	t.Helper()

	fake := &fakeS3{parts: map[int][]byte{}}
	server := httptest.NewServer(http.HandlerFunc(fake.handle))
	t.Cleanup(server.Close)

	return fake, server
}

func (f *fakeS3) handle(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	query := r.URL.Query()

	f.mu.Lock()
	if ct := r.Header.Get("Content-Type"); ct != "" && ct != "application/octet-stream" {
		f.contentType = ct
	}
	f.mu.Unlock()

	switch {
	case r.Method == http.MethodPost && query.Has("uploads"):
		writeXML(w, `<InitiateMultipartUploadResult><Bucket>b</Bucket><Key>k</Key><UploadId>UP1</UploadId></InitiateMultipartUploadResult>`)
	case r.Method == http.MethodPut && query.Has("partNumber"):
		partNumber, _ := strconv.Atoi(query.Get("partNumber"))
		f.mu.Lock()
		f.parts[partNumber] = body
		f.mu.Unlock()
		w.Header().Set("ETag", `"etag-`+query.Get("partNumber")+`"`)
		w.WriteHeader(http.StatusOK)
	case r.Method == http.MethodPost && query.Has("uploadId"):
		writeXML(w, `<CompleteMultipartUploadResult><Location>l</Location><Bucket>b</Bucket><Key>k</Key><ETag>&quot;final&quot;</ETag></CompleteMultipartUploadResult>`)
	case r.Method == http.MethodPut:
		f.mu.Lock()
		f.singlePut = body
		f.mu.Unlock()
		w.Header().Set("ETag", `"etag-single"`)
		w.WriteHeader(http.StatusOK)
	default:
		w.WriteHeader(http.StatusOK)
	}
}

func (f *fakeS3) uploadedObject() []byte {
	f.mu.Lock()
	defer f.mu.Unlock()

	if f.singlePut != nil {
		return f.singlePut
	}

	numbers := make([]int, 0, len(f.parts))
	for number := range f.parts {
		numbers = append(numbers, number)
	}
	sort.Ints(numbers)

	var joined []byte
	for _, number := range numbers {
		joined = append(joined, f.parts[number]...)
	}

	return joined
}

func (f *fakeS3) partSizes() []int {
	f.mu.Lock()
	defer f.mu.Unlock()

	numbers := make([]int, 0, len(f.parts))
	for number := range f.parts {
		numbers = append(numbers, number)
	}
	sort.Ints(numbers)

	sizes := make([]int, 0, len(numbers))
	for _, number := range numbers {
		sizes = append(sizes, len(f.parts[number]))
	}

	return sizes
}

func writeXML(w http.ResponseWriter, payload string) {
	w.Header().Set("Content-Type", "application/xml")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(payload))
}

func tarEntries(t *testing.T, archive []byte) map[string]int64 {
	t.Helper()

	entries := map[string]int64{}
	reader := tar.NewReader(bytes.NewReader(archive))

	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		require.NoError(t, err)
		entries[header.Name] = header.Size
	}

	return entries
}

func storageForServer(server *httptest.Server) *objectStorage {
	return NewObjectStorage(objectStorageConfig{
		EndpointURL:     server.URL,
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
		Region:          "us-east-1",
	})
}

func TestNewObjectStorage(t *testing.T) {
	tests := []struct {
		name           string
		config         objectStorageConfig
		expectedBucket string
		expectedRegion string
	}{
		{
			name: "basic config",
			config: objectStorageConfig{
				EndpointURL:     "https://s3.us-east-1.amazonaws.com",
				AccessKeyID:     "AKID",
				SecretAccessKey: "SECRET",
				BucketName:      "my-bucket",
				Region:          "us-east-1",
			},
			expectedBucket: "my-bucket",
			expectedRegion: "us-east-1",
		},
		{
			name: "default bucket name",
			config: objectStorageConfig{
				EndpointURL:     "https://s3.us-west-2.amazonaws.com",
				AccessKeyID:     "AKID",
				SecretAccessKey: "SECRET",
				BucketName:      "",
				Region:          "us-west-2",
			},
			expectedBucket: "daytona-volume-builds",
			expectedRegion: "us-west-2",
		},
		{
			name: "with session token",
			config: objectStorageConfig{
				EndpointURL:     "https://s3.eu-west-1.amazonaws.com",
				AccessKeyID:     "AKID",
				SecretAccessKey: "SECRET",
				SessionToken:    strPtr("session-token"),
				BucketName:      "test-bucket",
				Region:          "eu-west-1",
			},
			expectedBucket: "test-bucket",
			expectedRegion: "eu-west-1",
		},
		{
			name: "non-aws endpoint",
			config: objectStorageConfig{
				EndpointURL:     "https://minio.local:9000",
				AccessKeyID:     "minioadmin",
				SecretAccessKey: "minioadmin",
				BucketName:      "builds",
				Region:          "auto",
			},
			expectedBucket: "builds",
			expectedRegion: "auto",
		},
		{
			name: "region is passed through regardless of endpoint host",
			config: objectStorageConfig{
				EndpointURL:     "https://s3.us-west-2.amazonaws.com",
				AccessKeyID:     "AKID",
				SecretAccessKey: "SECRET",
				BucketName:      "builds",
				Region:          "us-east-2",
			},
			expectedBucket: "builds",
			expectedRegion: "us-east-2",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			objStorage := NewObjectStorage(tt.config)
			require.NotNil(t, objStorage)
			assert.Equal(t, tt.expectedBucket, objStorage.bucketName)
			require.NotNil(t, objStorage.client)
			assert.Equal(t, tt.expectedRegion, objStorage.client.Options().Region)
		})
	}
}

func TestComputeHashForFile(t *testing.T) {
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "test.txt")
	err := os.WriteFile(tmpFile, []byte("hello world"), 0644)
	require.NoError(t, err)

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	hash, err := objStorage.computeHashForPath(tmpFile, "test.txt")
	require.NoError(t, err)
	assert.NotEmpty(t, hash)
	assert.Len(t, hash, 32)
}

func TestComputeHashForDirectory(t *testing.T) {
	tmpDir := t.TempDir()
	subDir := filepath.Join(tmpDir, "subdir")
	err := os.Mkdir(subDir, 0755)
	require.NoError(t, err)

	err = os.WriteFile(filepath.Join(subDir, "file1.txt"), []byte("content1"), 0644)
	require.NoError(t, err)
	err = os.WriteFile(filepath.Join(subDir, "file2.txt"), []byte("content2"), 0644)
	require.NoError(t, err)

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	hash, err := objStorage.computeHashForPath(subDir, "context")
	require.NoError(t, err)
	assert.NotEmpty(t, hash)
	assert.Len(t, hash, 32)
}

func TestComputeHashDeterministic(t *testing.T) {
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "test.txt")
	err := os.WriteFile(tmpFile, []byte("deterministic content"), 0644)
	require.NoError(t, err)

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	hash1, err := objStorage.computeHashForPath(tmpFile, "test.txt")
	require.NoError(t, err)

	hash2, err := objStorage.computeHashForPath(tmpFile, "test.txt")
	require.NoError(t, err)

	assert.Equal(t, hash1, hash2)
}

func TestComputeHashDifferentContent(t *testing.T) {
	tmpDir := t.TempDir()
	file1 := filepath.Join(tmpDir, "file1.txt")
	file2 := filepath.Join(tmpDir, "file2.txt")
	err := os.WriteFile(file1, []byte("content A"), 0644)
	require.NoError(t, err)
	err = os.WriteFile(file2, []byte("content B"), 0644)
	require.NoError(t, err)

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	hash1, err := objStorage.computeHashForPath(file1, "file.txt")
	require.NoError(t, err)

	hash2, err := objStorage.computeHashForPath(file2, "file.txt")
	require.NoError(t, err)

	assert.NotEqual(t, hash1, hash2)
}

func TestStatPathNonExistent(t *testing.T) {
	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	_, err := objStorage.statPath("/nonexistent/path")
	require.Error(t, err)
}

func TestStatPathExistent(t *testing.T) {
	tmpFile := filepath.Join(t.TempDir(), "exists.txt")
	err := os.WriteFile(tmpFile, []byte("exists"), 0644)
	require.NoError(t, err)

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	info, err := objStorage.statPath(tmpFile)
	require.NoError(t, err)
	assert.Equal(t, "exists.txt", info.Name())
}

func TestPushAccessCredentialsStruct(t *testing.T) {
	creds := PushAccessCredentials{
		StorageURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKey:      "AKID",
		Secret:         "SECRET",
		SessionToken:   "TOKEN",
		Bucket:         "my-bucket",
		OrganizationID: "org-1",
		Region:         "us-east-2",
	}

	assert.Equal(t, "https://s3.us-east-1.amazonaws.com", creds.StorageURL)
	assert.Equal(t, "AKID", creds.AccessKey)
	assert.Equal(t, "SECRET", creds.Secret)
	assert.Equal(t, "TOKEN", creds.SessionToken)
	assert.Equal(t, "my-bucket", creds.Bucket)
	assert.Equal(t, "org-1", creds.OrganizationID)
	assert.Equal(t, "us-east-2", creds.Region)
}

func TestObjectStorageUploadValidations(t *testing.T) {
	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	_, err := objStorage.Upload(context.Background(), "/definitely/missing", "org-1", "ctx")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Path does not exist")
}

func TestObjectStorageHashIncludesArchiveBasePath(t *testing.T) {
	tmpFile := filepath.Join(t.TempDir(), "same.txt")
	require.NoError(t, os.WriteFile(tmpFile, []byte("same-content"), 0o644))

	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	hashA, err := objStorage.computeHashForPath(tmpFile, "a.txt")
	require.NoError(t, err)
	hashB, err := objStorage.computeHashForPath(tmpFile, "b.txt")
	require.NoError(t, err)
	assert.NotEqual(t, hashA, hashB)
}

func TestNewObjectStorageConfiguresUploadTimeoutAndRetries(t *testing.T) {
	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
		Region:          "us-east-1",
	})

	client, ok := objStorage.client.Options().HTTPClient.(*awshttp.BuildableClient)
	require.True(t, ok)
	assert.Equal(t, 2*time.Minute, client.GetTimeout())

	require.NotNil(t, objStorage.client.Options().Retryer)
	assert.Equal(t, uploadMaxAttempts, objStorage.client.Options().Retryer.MaxAttempts())
}

// countRetryAttempts drives one request through a scaled-down copy of the shipped
// retry configuration and reports how many attempts actually reached the server.
func countRetryAttempts(t *testing.T, handler http.HandlerFunc, requestTimeout, budget time.Duration) int {
	t.Helper()

	var mu sync.Mutex
	attempts := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		attempts++
		mu.Unlock()
		handler(w, r)
	}))
	defer server.Close()

	client := s3.New(s3.Options{
		Region:       "us-east-1",
		Credentials:  credentials.NewStaticCredentialsProvider("AKID", "SECRET", ""),
		BaseEndpoint: aws.String(server.URL),
		UsePathStyle: true,
		HTTPClient:   awshttp.NewBuildableClient().WithTimeout(requestTimeout),
		Retryer: budgetRetryer{
			RetryerV2: retry.NewStandard(func(o *retry.StandardOptions) {
				o.MaxAttempts = uploadMaxAttempts
				o.MaxBackoff = 20 * time.Millisecond
			}),
			budget: budget,
		},
		APIOptions: []func(*middleware.Stack) error{withRetryBudgetClock},
	})

	_, _ = client.HeadObject(context.Background(), &s3.HeadObjectInput{
		Bucket: aws.String("test"),
		Key:    aws.String("probe"),
	})

	mu.Lock()
	defer mu.Unlock()

	return attempts
}

func TestRetryBudgetBoundsStalledConnections(t *testing.T) {
	// Hold the request open until the client's timeout closes the connection.
	stalled := countRetryAttempts(t, func(_ http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}, 200*time.Millisecond, 400*time.Millisecond)

	assert.Equal(t, 2, stalled, "a stall burns a full request timeout per attempt, so the budget must allow two")
}

func TestRetryBudgetKeepsAttemptsForFastFailures(t *testing.T) {
	fast := countRetryAttempts(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`<Error><Code>SlowDown</Code></Error>`))
	}, 200*time.Millisecond, 400*time.Millisecond)

	assert.Equal(t, uploadMaxAttempts, fast, "throttling responses cost almost no budget and must keep the full allowance")
}

// The upload credentials are short-lived, so the worst case a stalled connection can
// reach must stay under five minutes. Two attempts fit inside the budget, and each can
// be preceded by a backoff capped at uploadMaxBackoff.
func TestRetryBudgetStaysWithinCredentialLifetime(t *testing.T) {
	worstCase := 2*uploadRequestTimeout + 2*uploadMaxBackoff

	assert.Less(t, worstCase, 5*time.Minute)
	assert.Less(t, uploadRetryTimeout, 5*time.Minute)
}

func TestUploadAsTarStreamsLargeContextAsMultipart(t *testing.T) {
	fake, server := newFakeS3(t)

	tmpDir := t.TempDir()
	// Two files of 6 MiB each force at least three 5 MiB parts.
	for _, name := range []string{"big1.bin", "big2.bin"} {
		require.NoError(t, os.WriteFile(filepath.Join(tmpDir, name), bytes.Repeat([]byte("x"), 6*1024*1024), 0o644))
	}

	err := storageForServer(server).uploadAsTar(context.Background(), "org/hash/context.tar", tmpDir, "context")
	require.NoError(t, err)

	sizes := fake.partSizes()
	require.GreaterOrEqual(t, len(sizes), 2, "large context must be uploaded as multiple parts")

	for _, size := range sizes[:len(sizes)-1] {
		assert.Equal(t, uploadPartSize, size, "every part but the last must match the configured part size")
	}

	entries := tarEntries(t, fake.uploadedObject())
	assert.Equal(t, int64(6*1024*1024), entries[filepath.Join("context", "big1.bin")])
	assert.Equal(t, int64(6*1024*1024), entries[filepath.Join("context", "big2.bin")])
}

func TestUploadAsTarUsesSingleRequestForSmallContext(t *testing.T) {
	fake, server := newFakeS3(t)

	tmpFile := filepath.Join(t.TempDir(), "small.txt")
	require.NoError(t, os.WriteFile(tmpFile, []byte("small content"), 0o644))

	err := storageForServer(server).uploadAsTar(context.Background(), "org/hash/context.tar", tmpFile, "small.txt")
	require.NoError(t, err)

	assert.Empty(t, fake.partSizes(), "a context below the part size must not open a multipart upload")

	entries := tarEntries(t, fake.uploadedObject())
	assert.Equal(t, int64(len("small content")), entries["small.txt"])
	assert.Equal(t, "application/x-tar", fake.contentType)
}

func TestObjectStorageUploadAsTarInvalidPath(t *testing.T) {
	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     "https://s3.us-east-1.amazonaws.com",
		AccessKeyID:     "AKID",
		SecretAccessKey: "SECRET",
		BucketName:      "test",
	})

	err := objStorage.uploadAsTar(context.Background(), "key.tar", "/missing/file", "archive-base")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Failed to stat path")
}
