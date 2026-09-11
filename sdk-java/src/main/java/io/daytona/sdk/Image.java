// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.sdk.exception.DaytonaNotFoundException;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.StringJoiner;

/**
 * Declarative image builder used to define Sandbox runtime environments.
 *
 * <p>Use factory methods such as {@link #base(String)} or {@link #debianSlim(String)} and chain
 * mutating methods to append Dockerfile instructions.
 *
 * <p>Local files and directories added with {@link #addLocalFile(String, String)} and
 * {@link #addLocalDir(String, String)} are uploaded to Daytona object storage as build contexts
 * when the image is used to create a snapshot or a Sandbox.
 */
public class Image {
    /**
     * A local file or directory that is part of the image build context.
     *
     * <p>The source path is uploaded to object storage and made available inside the build context
     * under {@link #getArchivePath()}, which is the path referenced by the generated {@code COPY}
     * instruction.
     */
    public static final class Context {
        private final String sourcePath;
        private final String archivePath;

        Context(String sourcePath, String archivePath) {
            this.sourcePath = sourcePath;
            this.archivePath = archivePath;
        }

        /**
         * Returns the local filesystem path of the file or directory.
         *
         * @return absolute local path
         */
        public String getSourcePath() {
            return sourcePath;
        }

        /**
         * Returns the path of the entry within the uploaded build context archive.
         *
         * @return archive-relative path
         */
        public String getArchivePath() {
            return archivePath;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof Context)) return false;
            Context other = (Context) o;
            return sourcePath.equals(other.sourcePath) && archivePath.equals(other.archivePath);
        }

        @Override
        public int hashCode() {
            return Objects.hash(sourcePath, archivePath);
        }

        @Override
        public String toString() {
            return "Context{sourcePath='" + sourcePath + "', archivePath='" + archivePath + "'}";
        }
    }

    private final StringBuilder dockerfile = new StringBuilder();
    private final List<Context> contexts = new ArrayList<>();

    private Image() {}

    /**
     * Creates an image definition from an existing base image.
     *
     * @param baseImage base image reference (for example {@code python:3.12-slim-bookworm})
     * @return new {@link Image} initialized with a {@code FROM} instruction
     */
    public static Image base(String baseImage) {
        Image image = new Image();
        image.dockerfile.append("FROM ").append(baseImage).append("\n");
        return image;
    }

    /**
     * Creates a Python Debian slim image.
     *
     * @param pythonVersion Python version to use; defaults to {@code 3.11} when {@code null} or empty
     * @return new {@link Image} using a Python slim base image
     */
    public static Image debianSlim(String pythonVersion) {
        String version = pythonVersion == null || pythonVersion.isEmpty() ? "3.11" : pythonVersion;
        return base("python:" + version + "-slim");
    }

    /**
     * Adds a {@code pip install} instruction for one or more packages.
     *
     * @param packages package names to install
     * @return this {@link Image} for method chaining
     */
    public Image pipInstall(String... packages) {
        if (packages == null || packages.length == 0) {
            return this;
        }
        StringJoiner joiner = new StringJoiner(" ");
        for (String pkg : packages) {
            joiner.add(pkg);
        }
        dockerfile.append("RUN pip install ").append(joiner.toString()).append("\n");
        return this;
    }

    /**
     * Adds one or more {@code RUN} instructions.
     *
     * @param commands shell commands to execute during image build
     * @return this {@link Image} for method chaining
     */
    public Image runCommands(String... commands) {
        if (commands == null || commands.length == 0) {
            return this;
        }
        for (String cmd : commands) {
            dockerfile.append("RUN ").append(cmd).append("\n");
        }
        return this;
    }

    /**
     * Adds environment variables using {@code ENV} instructions.
     *
     * @param envVars environment variables to set in the image
     * @return this {@link Image} for method chaining
     */
    public Image env(Map<String, String> envVars) {
        if (envVars == null) {
            return this;
        }
        for (Map.Entry<String, String> e : envVars.entrySet()) {
            dockerfile.append("ENV ").append(e.getKey()).append("=\"").append(e.getValue().replace("\"", "\\\"")).append("\"\n");
        }
        return this;
    }

    /**
     * Sets the default working directory using a {@code WORKDIR} instruction.
     *
     * @param path working directory path
     * @return this {@link Image} for method chaining
     */
    public Image workdir(String path) {
        dockerfile.append("WORKDIR ").append(path).append("\n");
        return this;
    }

    /**
     * Sets the container entrypoint.
     *
     * @param commands entrypoint command and arguments
     * @return this {@link Image} for method chaining
     */
    public Image entrypoint(String... commands) {
        dockerfile.append("ENTRYPOINT ").append(jsonArray(commands)).append("\n");
        return this;
    }

    /**
     * Sets the default container command.
     *
     * @param commands default command and arguments
     * @return this {@link Image} for method chaining
     */
    public Image cmd(String... commands) {
        dockerfile.append("CMD ").append(jsonArray(commands)).append("\n");
        return this;
    }

    /**
     * Adds a local file to the image.
     *
     * <p>The file is uploaded to Daytona object storage as part of the build context when the image
     * is used to create a snapshot or a Sandbox, and copied to {@code remotePath} with a
     * {@code COPY} instruction. If {@code remotePath} ends with {@code /}, the local file name is
     * appended to it.
     *
     * <pre>{@code
     * Image image = Image.debianSlim("3.12")
     *     .addLocalFile("requirements.txt", "/home/daytona/requirements.txt");
     * }</pre>
     *
     * @param localPath path to the local file; a leading {@code ~} is expanded to the user home
     * @param remotePath destination path inside the image
     * @return this {@link Image} for method chaining
     * @throws DaytonaNotFoundException if {@code localPath} does not exist
     * @throws IllegalArgumentException if {@code localPath} exists but is not a regular file
     */
    public Image addLocalFile(String localPath, String remotePath) {
        Path expanded = expandUserHome(localPath);
        if (!Files.exists(expanded)) {
            throw new DaytonaNotFoundException("Local file " + localPath + " does not exist");
        }
        if (!Files.isRegularFile(expanded)) {
            throw new IllegalArgumentException("Local path " + localPath + " exists but is not a file");
        }
        String destination = remotePath;
        if (destination.endsWith("/")) {
            destination = destination + expanded.getFileName();
        }
        return addContext(expanded, destination);
    }

    /**
     * Adds a local directory to the image.
     *
     * <p>The directory is uploaded to Daytona object storage as part of the build context when the
     * image is used to create a snapshot or a Sandbox, and copied to {@code remotePath} with a
     * {@code COPY} instruction.
     *
     * <pre>{@code
     * Image image = Image.debianSlim("3.12").addLocalDir("src", "/home/daytona/src");
     * }</pre>
     *
     * @param localPath path to the local directory; a leading {@code ~} is expanded to the user home
     * @param remotePath destination path inside the image
     * @return this {@link Image} for method chaining
     * @throws DaytonaNotFoundException if {@code localPath} does not exist
     * @throws IllegalArgumentException if {@code localPath} exists but is not a directory
     */
    public Image addLocalDir(String localPath, String remotePath) {
        Path expanded = expandUserHome(localPath);
        if (!Files.exists(expanded)) {
            throw new DaytonaNotFoundException("Local directory " + localPath + " does not exist");
        }
        if (!Files.isDirectory(expanded)) {
            throw new IllegalArgumentException("Local path " + localPath + " exists but is not a directory");
        }
        return addContext(expanded, remotePath);
    }

    private Image addContext(Path source, String remotePath) {
        Path absolute = source.toAbsolutePath().normalize();
        String archivePath = ObjectStorage.computeArchiveBasePath(absolute);
        contexts.add(new Context(absolute.toString(), archivePath));
        dockerfile.append("COPY ").append(archivePath).append(" ").append(remotePath).append("\n");
        return this;
    }

    private static Path expandUserHome(String path) {
        Objects.requireNonNull(path, "localPath");
        if (path.equals("~") || path.startsWith("~/") || path.startsWith("~\\")) {
            return Paths.get(System.getProperty("user.home"), path.substring(1));
        }
        return Paths.get(path);
    }

    /**
     * Returns generated Dockerfile content.
     *
     * @return Dockerfile text assembled by this builder
     */
    public String getDockerfile() {
        return dockerfile.toString();
    }

    /**
     * Returns the local build contexts registered with {@link #addLocalFile(String, String)} and
     * {@link #addLocalDir(String, String)}, in insertion order.
     *
     * @return unmodifiable list of build contexts
     */
    public List<Context> getContexts() {
        return Collections.unmodifiableList(contexts);
    }

    private String jsonArray(String... values) {
        StringJoiner joiner = new StringJoiner(",", "[", "]");
        if (values != null) {
            for (String v : values) {
                joiner.add("\"" + v.replace("\"", "\\\"") + "\"");
            }
        }
        return joiner.toString();
    }
}
