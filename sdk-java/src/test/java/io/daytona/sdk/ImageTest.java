// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.sdk.exception.DaytonaNotFoundException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ImageTest {

    @Test
    void baseCreatesFromInstruction() {
        assertThat(Image.base("python:3.12").getDockerfile())
                .isEqualTo("FROM python:3.12\n");
    }

    @Test
    void debianSlimUsesDefaultVersion() {
        assertThat(Image.debianSlim(null).getDockerfile())
                .isEqualTo("FROM python:3.11-slim\n");
    }

    @Test
    void debianSlimUsesProvidedVersion() {
        assertThat(Image.debianSlim("3.12").getDockerfile())
                .isEqualTo("FROM python:3.12-slim\n");
    }

    @Test
    void pipInstallIgnoresEmptyPackages() {
        Image image = Image.base("python:3.12").pipInstall();

        assertThat(image.getDockerfile()).isEqualTo("FROM python:3.12\n");
    }

    @Test
    void runCommandsAndEnvIgnoreNullValues() {
        Image image = Image.base("python:3.12")
                .runCommands((String[]) null)
                .env(null);

        assertThat(image.getDockerfile()).isEqualTo("FROM python:3.12\n");
    }

    @Test
    void envEscapesQuotes() {
        Map<String, String> env = new LinkedHashMap<String, String>();
        env.put("NAME", "va\"lue");

        assertThat(Image.base("python:3.12").env(env).getDockerfile())
                .contains("ENV NAME=\"va\\\"lue\"");
    }

    @Test
    void entrypointAndCmdEscapeQuotesAndAllowEmptyArrays() {
        String dockerfile = Image.base("python:3.12")
                .entrypoint("python", "say \"hi\"")
                .cmd((String[]) null)
                .getDockerfile();

        assertThat(dockerfile)
                .contains("ENTRYPOINT [\"python\",\"say \\\"hi\\\"\"]\n")
                .contains("CMD []\n");
    }

    @Test
    void workdirAppendsLiteralValue() {
        assertThat(Image.base("python:3.12").workdir("").getDockerfile())
                .isEqualTo("FROM python:3.12\nWORKDIR \n");
    }

    @Test
    void fluentMutationsAppendDockerfileLines() {
        String dockerfile = Image.base("python:3.12")
                .pipInstall("pytest", "requests")
                .runCommands("apt-get update", "apt-get install -y git")
                .workdir("/workspace")
                .entrypoint("python", "main.py")
                .cmd("--flag")
                .getDockerfile();

        assertThat(dockerfile)
                .contains("RUN pip install pytest requests\n")
                .contains("RUN apt-get update\n")
                .contains("RUN apt-get install -y git\n")
                .contains("WORKDIR /workspace\n")
                .contains("ENTRYPOINT [\"python\",\"main.py\"]\n")
                .contains("CMD [\"--flag\"]\n");
    }

    @Test
    void addLocalFileTracksContextAndEmitsCopy(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("requirements.txt"), "numpy\n".getBytes());
        String archivePath = ObjectStorage.computeArchiveBasePath(file);

        Image image = Image.base("python:3.12").addLocalFile(file.toString(), "/app/requirements.txt");

        assertThat(image.getDockerfile())
                .isEqualTo("FROM python:3.12\nCOPY " + archivePath + " /app/requirements.txt\n");
        assertThat(image.getContexts()).containsExactly(new Image.Context(file.toAbsolutePath().normalize().toString(), archivePath));
        assertThat(archivePath).doesNotStartWith("/");
    }

    @Test
    void addLocalFileAppendsFileNameWhenRemotePathIsDirectory(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("config.yaml"), "a: 1\n".getBytes());

        Image image = Image.base("alpine").addLocalFile(file.toString(), "/etc/app/");

        assertThat(image.getDockerfile()).endsWith(" /etc/app/config.yaml\n");
    }

    @Test
    void addLocalFileResolvesRelativePathAgainstWorkingDirectory(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("relative.txt"), "x".getBytes());
        Path relative = Path.of("").toAbsolutePath().relativize(file.toAbsolutePath());

        Image image = Image.base("alpine").addLocalFile(relative.toString(), "/relative.txt");

        assertThat(image.getContexts()).singleElement()
                .extracting(Image.Context::getSourcePath)
                .isEqualTo(file.toAbsolutePath().normalize().toString());
    }

    @Test
    void addLocalFileRejectsMissingFile(@TempDir Path dir) {
        String missing = dir.resolve("missing.txt").toString();

        assertThatThrownBy(() -> Image.base("alpine").addLocalFile(missing, "/x"))
                .isInstanceOf(DaytonaNotFoundException.class)
                .hasMessage("Local file " + missing + " does not exist");
    }

    @Test
    void addLocalFileRejectsDirectory(@TempDir Path dir) {
        assertThatThrownBy(() -> Image.base("alpine").addLocalFile(dir.toString(), "/x"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("is not a file");
    }

    @Test
    void addLocalDirTracksContextAndEmitsCopy(@TempDir Path dir) throws IOException {
        Path src = Files.createDirectories(dir.resolve("src"));
        Files.write(src.resolve("main.py"), "print(1)\n".getBytes());
        String archivePath = ObjectStorage.computeArchiveBasePath(src);

        Image image = Image.base("python:3.12").addLocalDir(src.toString(), "/app/src");

        assertThat(image.getDockerfile()).isEqualTo("FROM python:3.12\nCOPY " + archivePath + " /app/src\n");
        assertThat(image.getContexts()).containsExactly(new Image.Context(src.toAbsolutePath().normalize().toString(), archivePath));
    }

    @Test
    void addLocalDirRejectsMissingAndNonDirectory(@TempDir Path dir) throws IOException {
        Path file = Files.write(dir.resolve("file.txt"), "x".getBytes());

        assertThatThrownBy(() -> Image.base("alpine").addLocalDir(dir.resolve("nope").toString(), "/x"))
                .isInstanceOf(DaytonaNotFoundException.class);
        assertThatThrownBy(() -> Image.base("alpine").addLocalDir(file.toString(), "/x"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("is not a directory");
    }

    @Test
    void addLocalFileExpandsUserHome(@TempDir Path dir) throws IOException {
        String originalHome = System.getProperty("user.home");
        System.setProperty("user.home", dir.toString());
        try {
            Files.write(dir.resolve("home.txt"), "x".getBytes());

            Image image = Image.base("alpine").addLocalFile("~/home.txt", "/home.txt");

            assertThat(image.getContexts()).singleElement()
                    .extracting(Image.Context::getSourcePath)
                    .isEqualTo(dir.resolve("home.txt").toAbsolutePath().normalize().toString());
        } finally {
            System.setProperty("user.home", originalHome);
        }
    }

    @Test
    void imagesWithoutLocalFilesHaveNoContexts() {
        assertThat(Image.base("alpine").runCommands("true").getContexts()).isEmpty();
    }
}
