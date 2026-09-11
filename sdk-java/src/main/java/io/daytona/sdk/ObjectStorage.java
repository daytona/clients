// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.ObjectStorageApi;
import io.daytona.api.client.model.StorageAccessDto;
import io.daytona.sdk.exception.DaytonaException;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentials;
import software.amazon.awssdk.auth.credentials.AwsSessionCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.core.checksums.RequestChecksumCalculation;
import software.amazon.awssdk.core.checksums.ResponseChecksumValidation;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.HeadObjectRequest;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Uploads {@link Image} build contexts (local files and directories) to Daytona object storage.
 *
 * <p>Each context is packed into a tar archive and stored under
 * {@code {organizationId}/{contentHash}/context.tar}. The content hash doubles as the identifier the
 * API expects in {@code contextHashes}; contexts whose archive already exists are not re-uploaded.
 *
 * <p>Used internally by {@link SnapshotService} and {@link Daytona} when an {@link Image} carries
 * contexts added via {@link Image#addLocalFile(String, String)} or
 * {@link Image#addLocalDir(String, String)}.
 */
final class ObjectStorage implements AutoCloseable {
    static final String DEFAULT_BUCKET = "daytona-volume-builds";
    static final String DEFAULT_REGION = "us-east-1";
    private static final String CONTEXT_ARCHIVE_NAME = "context.tar";
    private static final int DEFAULT_FILE_MODE = 0644;
    private static final int DEFAULT_DIR_MODE = 0755;

    private final S3Client s3;
    private final String bucket;

    /**
     * Creates a client from the temporary push-access credentials returned by the Daytona API.
     *
     * @param access credentials from {@code GET /object-storage/push-access}
     */
    ObjectStorage(StorageAccessDto access) {
        this(createS3Client(access), defaultIfBlank(access.getBucket(), DEFAULT_BUCKET));
    }

    ObjectStorage(S3Client s3, String bucket) {
        this.s3 = s3;
        this.bucket = bucket;
    }

    private static S3Client createS3Client(StorageAccessDto access) {
        AwsCredentials credentials;
        if (access.getSessionToken() == null || access.getSessionToken().isEmpty()) {
            credentials = AwsBasicCredentials.create(access.getAccessKey(), access.getSecret());
        } else {
            credentials = AwsSessionCredentials.create(access.getAccessKey(), access.getSecret(), access.getSessionToken());
        }
        return S3Client.builder()
                .httpClientBuilder(UrlConnectionHttpClient.builder())
                .credentialsProvider(StaticCredentialsProvider.create(credentials))
                .region(Region.of(defaultIfBlank(access.getRegion(), DEFAULT_REGION)))
                .endpointOverride(URI.create(access.getStorageUrl()))
                // Path-style addressing and a plain (non aws-chunked) body keep uploads compatible
                // with every S3-compatible backend Daytona may hand out push access for.
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(true)
                        .chunkedEncodingEnabled(false)
                        .build())
                .requestChecksumCalculation(RequestChecksumCalculation.WHEN_REQUIRED)
                .responseChecksumValidation(ResponseChecksumValidation.WHEN_REQUIRED)
                .build();
    }

    private static String defaultIfBlank(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    /**
     * Uploads every build context of an {@link Image} and returns their hashes in context order.
     *
     * <p>Returns an empty list without contacting the API when the image has no contexts.
     *
     * @param objectStorageApi API used to obtain temporary push credentials
     * @param image image whose contexts should be uploaded
     * @return context hashes to send as {@code CreateBuildInfo.contextHashes}
     * @throws DaytonaException if credentials cannot be obtained or an upload fails
     */
    static List<String> processImageContext(ObjectStorageApi objectStorageApi, Image image) {
        List<Image.Context> contexts = image.getContexts();
        if (contexts.isEmpty()) {
            return Collections.emptyList();
        }
        StorageAccessDto access = ExceptionMapper.callMain(() -> objectStorageApi.getPushAccess(null));
        if (access == null) {
            throw new DaytonaException("Object storage push access response was empty");
        }
        List<String> hashes = new ArrayList<>(contexts.size());
        try (ObjectStorage storage = new ObjectStorage(access)) {
            for (Image.Context context : contexts) {
                hashes.add(storage.upload(Paths.get(context.getSourcePath()), access.getOrganizationId(), context.getArchivePath()));
            }
        }
        return hashes;
    }

    /**
     * Uploads a local file or directory as a tar archive and returns its content hash.
     *
     * @param path local file or directory
     * @param organizationId organization that owns the storage prefix
     * @param archiveBasePath name of the entry (or root directory) inside the archive
     * @return MD5 content hash identifying the uploaded context
     * @throws DaytonaException if the path does not exist or the upload fails
     */
    String upload(Path path, String organizationId, String archiveBasePath) {
        if (!Files.exists(path)) {
            throw new DaytonaException("Path does not exist: " + path);
        }
        String hash = computeHash(path, archiveBasePath);
        String key = organizationId + "/" + hash + "/" + CONTEXT_ARCHIVE_NAME;
        if (!objectExists(key)) {
            uploadAsTar(key, path, archiveBasePath);
        }
        return hash;
    }

    /**
     * Computes the archive-relative name for a local path: the normalized absolute path without its
     * root component (leading {@code /} or Windows drive letter). Matches the other Daytona SDKs so
     * the generated {@code COPY} instruction resolves inside the build context.
     *
     * @param path local path
     * @return archive base path, never empty
     */
    static String computeArchiveBasePath(Path path) {
        Path absolute = path.toAbsolutePath().normalize();
        Path root = absolute.getRoot();
        Path relative = root == null ? absolute : root.relativize(absolute);
        String archivePath = relative.toString().replace('\\', '/');
        while (archivePath.startsWith("/")) {
            archivePath = archivePath.substring(1);
        }
        return archivePath.isEmpty() ? absolute.getFileName().toString() : archivePath;
    }

    /**
     * Computes the MD5 hash of a file or directory tree together with its archive base path.
     *
     * <p>For directories, entries are visited in lexicographic order so the hash is stable across
     * runs and platforms. Each file contributes its archive-relative path and contents; empty
     * directories contribute their relative path only.
     */
    static String computeHash(Path path, String archiveBasePath) {
        MessageDigest md5 = newMd5();
        md5.update(archiveBasePath.getBytes(StandardCharsets.UTF_8));
        try {
            if (Files.isDirectory(path)) {
                for (Path entry : sortedTree(path)) {
                    if (entry.equals(path)) {
                        continue;
                    }
                    String relative = toArchiveRelative(path, entry);
                    if (Files.isDirectory(entry)) {
                        if (isEmptyDirectory(entry)) {
                            md5.update(relative.getBytes(StandardCharsets.UTF_8));
                        }
                        continue;
                    }
                    md5.update(relative.getBytes(StandardCharsets.UTF_8));
                    digestFile(md5, entry);
                }
            } else {
                digestFile(md5, path);
            }
        } catch (IOException e) {
            throw new DaytonaException("Failed to hash " + path + ": " + e.getMessage(), e);
        }
        return toHex(md5.digest());
    }

    private boolean objectExists(String key) {
        try {
            s3.headObject(HeadObjectRequest.builder().bucket(bucket).key(key).build());
            return true;
        } catch (NoSuchKeyException e) {
            return false;
        } catch (S3Exception e) {
            if (e.statusCode() == 404) {
                return false;
            }
            throw new DaytonaException("Failed to check object storage for " + key + ": " + e.getMessage(), e);
        }
    }

    private void uploadAsTar(String key, Path source, String archiveBasePath) {
        Path archive = null;
        try {
            archive = Files.createTempFile("daytona-context-", ".tar");
            try (OutputStream out = Files.newOutputStream(archive);
                 TarArchiveOutputStream tar = new TarArchiveOutputStream(out)) {
                tar.setLongFileMode(TarArchiveOutputStream.LONGFILE_POSIX);
                tar.setBigNumberMode(TarArchiveOutputStream.BIGNUMBER_POSIX);
                writeTree(tar, source, archiveBasePath);
                tar.finish();
            }
            s3.putObject(
                    PutObjectRequest.builder().bucket(bucket).key(key).contentType("application/x-tar").build(),
                    RequestBody.fromFile(archive));
        } catch (IOException e) {
            throw new DaytonaException("Failed to create build context archive for " + source + ": " + e.getMessage(), e);
        } catch (S3Exception e) {
            throw new DaytonaException("Failed to upload build context " + key + ": " + e.getMessage(), e);
        } finally {
            if (archive != null) {
                try {
                    Files.deleteIfExists(archive);
                } catch (IOException ignored) {
                    // Best effort; the temp directory is cleaned up by the OS.
                }
            }
        }
    }

    private static void writeTree(TarArchiveOutputStream tar, Path source, String archiveBasePath) throws IOException {
        if (Files.isDirectory(source)) {
            for (Path entry : sortedTree(source)) {
                String name = entry.equals(source)
                        ? archiveBasePath
                        : archiveBasePath + "/" + toArchiveRelative(source, entry);
                writeEntry(tar, entry, name);
            }
        } else {
            writeEntry(tar, source, archiveBasePath);
        }
    }

    private static void writeEntry(TarArchiveOutputStream tar, Path path, String name) throws IOException {
        boolean directory = Files.isDirectory(path);
        TarArchiveEntry entry = new TarArchiveEntry(directory ? name + "/" : name);
        entry.setModTime(Files.getLastModifiedTime(path).toMillis());
        entry.setMode(fileMode(path, directory));
        if (!directory) {
            entry.setSize(Files.size(path));
        }
        tar.putArchiveEntry(entry);
        if (!directory) {
            try (InputStream in = Files.newInputStream(path)) {
                in.transferTo(tar);
            }
        }
        tar.closeArchiveEntry();
    }

    private static int fileMode(Path path, boolean directory) {
        try {
            Set<PosixFilePermission> permissions = Files.getPosixFilePermissions(path);
            int mode = 0;
            for (PosixFilePermission permission : permissions) {
                mode |= posixBit(permission);
            }
            return mode;
        } catch (UnsupportedOperationException | IOException e) {
            return directory ? DEFAULT_DIR_MODE : DEFAULT_FILE_MODE;
        }
    }

    private static int posixBit(PosixFilePermission permission) {
        switch (permission) {
            case OWNER_READ: return 0400;
            case OWNER_WRITE: return 0200;
            case OWNER_EXECUTE: return 0100;
            case GROUP_READ: return 0040;
            case GROUP_WRITE: return 0020;
            case GROUP_EXECUTE: return 0010;
            case OTHERS_READ: return 0004;
            case OTHERS_WRITE: return 0002;
            case OTHERS_EXECUTE: return 0001;
            default: return 0;
        }
    }

    private static List<Path> sortedTree(Path root) throws IOException {
        try (Stream<Path> stream = Files.walk(root)) {
            List<Path> paths = new ArrayList<>();
            stream.forEach(paths::add);
            Collections.sort(paths);
            return paths;
        }
    }

    private static boolean isEmptyDirectory(Path directory) throws IOException {
        try (Stream<Path> children = Files.list(directory)) {
            return !children.findAny().isPresent();
        }
    }

    private static String toArchiveRelative(Path root, Path entry) {
        return root.relativize(entry).toString().replace('\\', '/');
    }

    private static void digestFile(MessageDigest digest, Path file) throws IOException {
        try (InputStream in = Files.newInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = in.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
    }

    private static MessageDigest newMd5() {
        try {
            return MessageDigest.getInstance("MD5");
        } catch (NoSuchAlgorithmException e) {
            throw new DaytonaException("MD5 digest is not available", e);
        }
    }

    private static String toHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(Character.forDigit((b >> 4) & 0xF, 16)).append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }

    @Override
    public void close() {
        s3.close();
    }
}
