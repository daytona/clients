// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.ApiException;
import io.daytona.api.client.api.ObjectStorageApi;
import io.daytona.api.client.model.StorageAccessDto;
import io.daytona.sdk.exception.DaytonaException;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.HeadObjectRequest;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectResponse;
import software.amazon.awssdk.services.s3.model.S3Exception;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ObjectStorageTest {

    @Mock
    private S3Client s3;

    @Mock
    private ObjectStorageApi objectStorageApi;

    private Map<String, String> uploadedTar;

    private void captureUploadedTar() {
        when(s3.putObject(any(PutObjectRequest.class), any(RequestBody.class))).thenAnswer(invocation -> {
            uploadedTar = readTar(invocation.getArgument(1, RequestBody.class).contentStreamProvider().newStream());
            return PutObjectResponse.builder().build();
        });
    }

    @Test
    void computeArchiveBasePathStripsRootAndNormalizes(@TempDir Path dir) {
        Path file = dir.resolve("sub/../a.txt");

        String archivePath = ObjectStorage.computeArchiveBasePath(file);

        assertThat(archivePath).doesNotStartWith("/").doesNotContain("..").endsWith("/a.txt");
        assertThat(Path.of("/" + archivePath)).isEqualTo(dir.resolve("a.txt").toAbsolutePath().normalize());
    }

    @Test
    void computeArchiveBasePathRejectsFilesystemRoot() {
        assertThatThrownBy(() -> ObjectStorage.computeArchiveBasePath(Path.of("/")))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void uploadHashIsStableAcrossRunsAndSensitiveToContentArchivePathAndMode(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenReturn(HeadObjectResponse.builder().build());
        ObjectStorage storage = new ObjectStorage(s3, "bucket");

        String first = storage.upload(file, "org", "ctx/a.txt");
        Files.setLastModifiedTime(file, java.nio.file.attribute.FileTime.fromMillis(0));
        String afterTouch = storage.upload(file, "org", "ctx/a.txt");
        String otherArchivePath = storage.upload(file, "org", "other/a.txt");
        Files.setPosixFilePermissions(file, java.util.EnumSet.of(
                java.nio.file.attribute.PosixFilePermission.OWNER_READ,
                java.nio.file.attribute.PosixFilePermission.OWNER_WRITE,
                java.nio.file.attribute.PosixFilePermission.OWNER_EXECUTE));
        String afterChmod = storage.upload(file, "org", "ctx/a.txt");
        Files.write(file, "changed".getBytes(StandardCharsets.UTF_8));
        String afterContentChange = storage.upload(file, "org", "ctx/a.txt");

        assertThat(first).matches("[0-9a-f]{32}").isEqualTo(afterTouch);
        assertThat(otherArchivePath).isNotEqualTo(first);
        assertThat(afterChmod).isNotEqualTo(first);
        assertThat(afterContentChange).isNotEqualTo(afterChmod);
    }

    @Test
    void uploadHashDistinguishesDirectoryLayoutsWithIdenticalConcatenation(@TempDir Path dir) throws IOException {
        Path first = Files.createDirectories(dir.resolve("first"));
        Files.write(first.resolve("ab"), "c".getBytes(StandardCharsets.UTF_8));
        Path second = Files.createDirectories(dir.resolve("second"));
        Files.write(second.resolve("a"), "bc".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenReturn(HeadObjectResponse.builder().build());
        ObjectStorage storage = new ObjectStorage(s3, "bucket");

        assertThat(storage.upload(first, "org", "root")).isNotEqualTo(storage.upload(second, "org", "root"));
    }

    @Test
    void uploadHashMatchesUploadedArchiveBytes(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenThrow(NoSuchKeyException.builder().statusCode(404).build());
        java.util.concurrent.atomic.AtomicReference<byte[]> uploaded = new java.util.concurrent.atomic.AtomicReference<>();
        when(s3.putObject(any(PutObjectRequest.class), any(RequestBody.class))).thenAnswer(invocation -> {
            uploaded.set(invocation.getArgument(1, RequestBody.class).contentStreamProvider().newStream().readAllBytes());
            return PutObjectResponse.builder().build();
        });

        String hash = new ObjectStorage(s3, "bucket").upload(file, "org", "ctx/a.txt");

        java.security.MessageDigest md5;
        try {
            md5 = java.security.MessageDigest.getInstance("MD5");
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        StringBuilder expected = new StringBuilder();
        for (byte b : md5.digest(uploaded.get())) {
            expected.append(String.format("%02x", b));
        }
        assertThat(hash).isEqualTo(expected.toString());
    }

    @Test
    void uploadSkipsPutWhenArchiveAlreadyExists(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenReturn(HeadObjectResponse.builder().build());

        String hash = new ObjectStorage(s3, "bucket").upload(file, "org-1", "ctx/a.txt");

        ArgumentCaptor<HeadObjectRequest> head = ArgumentCaptor.forClass(HeadObjectRequest.class);
        verify(s3).headObject(head.capture());
        assertThat(head.getValue().bucket()).isEqualTo("bucket");
        assertThat(head.getValue().key()).isEqualTo("org-1/" + hash + "/context.tar");
        verify(s3, never()).putObject(any(PutObjectRequest.class), any(RequestBody.class));
    }

    @Test
    void uploadPutsSingleFileAsTarWhenMissing(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenThrow(NoSuchKeyException.builder().statusCode(404).build());
        captureUploadedTar();

        String hash = new ObjectStorage(s3, "bucket").upload(file, "org-1", "ctx/a.txt");

        ArgumentCaptor<PutObjectRequest> put = ArgumentCaptor.forClass(PutObjectRequest.class);
        verify(s3).putObject(put.capture(), any(RequestBody.class));
        assertThat(put.getValue().key()).isEqualTo("org-1/" + hash + "/context.tar");
        assertThat(put.getValue().contentType()).isEqualTo("application/x-tar");
        assertThat(uploadedTar).containsExactly(Map.entry("ctx/a.txt", "hello"));
    }

    @Test
    void uploadPutsDirectoryTreeAsTar(@TempDir Path dir) throws IOException {
        Path root = Files.createDirectories(dir.resolve("root"));
        Files.write(root.resolve("b.txt"), "b".getBytes(StandardCharsets.UTF_8));
        Files.write(Files.createDirectories(root.resolve("nested")).resolve("a.txt"), "a".getBytes(StandardCharsets.UTF_8));
        Files.createDirectories(root.resolve("empty"));
        Files.createSymbolicLink(root.resolve("link-to-nested"), Path.of("nested"));
        Files.createSymbolicLink(root.resolve("link-to-b"), Path.of("b.txt"));
        when(s3.headObject(any(HeadObjectRequest.class))).thenThrow(S3Exception.builder().statusCode(404).build());
        captureUploadedTar();

        new ObjectStorage(s3, "bucket").upload(root, "org-1", "home/me/root");

        Map<String, String> entries = uploadedTar;
        assertThat(entries.keySet()).containsExactly(
                "home/me/root/", "home/me/root/b.txt", "home/me/root/empty/", "home/me/root/link-to-b",
                "home/me/root/link-to-nested", "home/me/root/nested/", "home/me/root/nested/a.txt");
        assertThat(entries).containsEntry("home/me/root/b.txt", "b").containsEntry("home/me/root/nested/a.txt", "a")
                .containsEntry("home/me/root/link-to-b", "-> b.txt").containsEntry("home/me/root/link-to-nested", "-> nested");
    }

    @Test
    void uploadWrapsStorageErrors(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class)))
                .thenThrow(S3Exception.builder().statusCode(403).message("denied").build());

        assertThatThrownBy(() -> new ObjectStorage(s3, "bucket").upload(file, "org-1", "ctx/a.txt"))
                .isInstanceOf(DaytonaException.class)
                .hasMessageContaining("Failed to check object storage");
    }

    @Test
    void uploadWrapsClientSideSdkErrors(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        when(s3.headObject(any(HeadObjectRequest.class))).thenThrow(NoSuchKeyException.builder().statusCode(404).build());
        when(s3.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
                .thenThrow(SdkClientException.create("connection refused"));

        assertThatThrownBy(() -> new ObjectStorage(s3, "bucket").upload(file, "org-1", "ctx/a.txt"))
                .isInstanceOf(DaytonaException.class)
                .hasMessageContaining("Failed to upload build context");
    }

    @Test
    void uploadRejectsMissingPath(@TempDir Path dir) {
        assertThatThrownBy(() -> new ObjectStorage(s3, "bucket").upload(dir.resolve("nope"), "org-1", "nope"))
                .isInstanceOf(DaytonaException.class)
                .hasMessageContaining("does not exist");
        verifyNoInteractions(s3);
    }

    @Test
    void processImageContextSkipsApiWhenImageHasNoContexts() {
        List<String> hashes = ObjectStorage.processImageContext(objectStorageApi, Image.base("alpine"));

        assertThat(hashes).isEmpty();
        verifyNoInteractions(objectStorageApi);
    }

    @Test
    void processImageContextMapsPushAccessErrors(@TempDir Path dir) throws Exception {
        Path file = Files.write(dir.resolve("a.txt"), "x".getBytes(StandardCharsets.UTF_8));
        when(objectStorageApi.getPushAccess(null)).thenThrow(new ApiException(403, "forbidden"));

        assertThatThrownBy(() -> ObjectStorage.processImageContext(objectStorageApi, Image.base("alpine").addLocalFile(file.toString(), "/a")))
                .isInstanceOf(DaytonaException.class);
    }

    @Test
    void processImageContextUploadsEachContextWithPushAccessCredentials(@TempDir Path dir) throws Exception {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        Path src = Files.createDirectories(dir.resolve("src"));
        Files.write(src.resolve("main.py"), "print(1)".getBytes(StandardCharsets.UTF_8));
        Image image = Image.base("python:3.12").addLocalFile(file.toString(), "/a.txt").addLocalDir(src.toString(), "/src");

        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setResponseCode(404));
            server.enqueue(new MockResponse().setResponseCode(200));
            server.enqueue(new MockResponse().setResponseCode(200));
            server.start();
            StorageAccessDto access = new StorageAccessDto()
                    .storageUrl(server.url("/").toString())
                    .accessKey("AKIA")
                    .secret("secret")
                    .sessionToken("token")
                    .bucket("my-bucket")
                    .organizationId("org-1")
                    .region("eu-west-1");
            when(objectStorageApi.getPushAccess(null)).thenReturn(access);

            List<String> hashes = ObjectStorage.processImageContext(objectStorageApi, image);

            List<Image.Context> contexts = image.getContexts();
            assertThat(hashes).hasSize(2).allMatch(hash -> hash.matches("[0-9a-f]{32}"));
            String fileHash = hashes.get(0);
            String dirHash = hashes.get(1);
            assertThat(fileHash).isNotEqualTo(dirHash);

            RecordedRequest head = server.takeRequest();
            assertThat(head.getMethod()).isEqualTo("HEAD");
            assertThat(head.getPath()).isEqualTo("/my-bucket/org-1/" + fileHash + "/context.tar");
            assertThat(head.getHeader("Authorization")).startsWith("AWS4-HMAC-SHA256 Credential=AKIA/").contains("/eu-west-1/s3/");
            assertThat(head.getHeader("x-amz-security-token")).isEqualTo("token");

            RecordedRequest put = server.takeRequest();
            assertThat(put.getMethod()).isEqualTo("PUT");
            assertThat(put.getPath()).isEqualTo("/my-bucket/org-1/" + fileHash + "/context.tar");
            assertThat(put.getHeader("Content-Type")).isEqualTo("application/x-tar");
            assertThat(put.getHeader("Content-Encoding")).isNull();
            assertThat(readTar(put.getBody().inputStream())).containsExactly(Map.entry(contexts.get(0).getArchivePath(), "hello"));

            RecordedRequest secondHead = server.takeRequest();
            assertThat(secondHead.getMethod()).isEqualTo("HEAD");
            assertThat(secondHead.getPath()).isEqualTo("/my-bucket/org-1/" + dirHash + "/context.tar");
            assertThat(server.getRequestCount()).isEqualTo(3);
        }
    }

    @Test
    void processImageContextFallsBackToDefaultBucketAndRegion(@TempDir Path dir) throws Exception {
        Path file = Files.write(dir.resolve("a.txt"), "hello".getBytes(StandardCharsets.UTF_8));
        Image image = Image.base("alpine").addLocalFile(file.toString(), "/a.txt");

        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse().setResponseCode(200));
            server.start();
            when(objectStorageApi.getPushAccess(null)).thenReturn(new StorageAccessDto()
                    .storageUrl(server.url("/").toString())
                    .accessKey("AKIA")
                    .secret("secret")
                    .organizationId("org-1"));

            ObjectStorage.processImageContext(objectStorageApi, image);

            RecordedRequest head = server.takeRequest();
            assertThat(head.getPath()).startsWith("/" + ObjectStorage.DEFAULT_BUCKET + "/org-1/");
            assertThat(head.getHeader("Authorization")).contains("/" + ObjectStorage.DEFAULT_REGION + "/s3/");
            assertThat(head.getHeader("x-amz-security-token")).isNull();
        }
    }

    private static Map<String, String> readTar(InputStream stream) throws IOException {
        Map<String, String> entries = new LinkedHashMap<>();
        try (TarArchiveInputStream tar = new TarArchiveInputStream(stream)) {
            TarArchiveEntry entry;
            while ((entry = tar.getNextEntry()) != null) {
                if (entry.isSymbolicLink()) {
                    entries.put(entry.getName(), "-> " + entry.getLinkName());
                } else {
                    entries.put(entry.getName(), entry.isDirectory() ? "" : new String(tar.readAllBytes(), StandardCharsets.UTF_8));
                }
            }
        }
        return entries;
    }
}
