// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newImageImportCommand() *cobra.Command {
	ref := ""
	cmd := &cobra.Command{
		Use:   "create <name>",
		Short: "Import an image from a registry ref",
		Long: "Import an image from a registry ref.\n" +
			imageNameRulesHelp +
			"A name that already exists for different content or import options (ref, region, CPU architecture) answers\n" +
			"409 IMAGE_NAME_TAKEN and changes nothing; repeating an identical import returns the existing image.\n" +
			"Example:\n  daytona image create my-base --ref python:3.12",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if cmd.Flags().Changed("ref") && strings.TrimSpace(ref) == "" {
				return cerr.Usage("--ref must be a non-empty registry reference")
			}
			body, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			if _, ok := lookupField(body, "dockerfile"); ok {
				return cerr.Usage("--file: \"dockerfile\" is not a field of image create").
					WithHint("build from a Dockerfile with `daytona image build <name> --context DIR`")
			}
			body.Set("name", args[0])
			if a.Globals.Region != "" {
				body.Set("region_id", a.Globals.Region)
			}
			workload.SetIfChanged(body, cmd.Flags(), "ref", "source", map[string]any{"type": string(gen.PublicImageSource0TypeRegistry), "ref": ref})
			req, err := workload.DecodeBody[gen.CreateImageRequest](body)
			if err != nil {
				return err
			}
			if source, ok := body["source"]; !ok || source == nil {
				return cerr.Usage("--ref is required")
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var created api.Raw[gen.AsyncOperation]
			if err := client.Do(cmd.Context(), http.MethodPost, "/images", nil, req, &created); err != nil {
				return err
			}
			return printCreatedImage(cmd.Context(), a, client, created)
		},
	}
	cmd.Flags().StringVar(&ref, "ref", "", "registry ref to import")
	// Not MarkFlagRequired: `source` may come from --file (the RunE checks
	// the merged body).
	addRegionFlag(cmd, "region to create the image in (default: the project's, then the organization's, then the platform's)")
	addFileFlag(cmd, "")
	addWaitFlags(cmd)
	return cmd
}

func newImageBuildCommand() *cobra.Command {
	dockerfilePath := ""
	contextPath := ""
	cmd := &cobra.Command{
		Use:   "build <name>",
		Short: "Build an image from a Dockerfile context",
		Long: "Build an image from a Dockerfile context: a directory, or a tar file uploaded as is.\n" +
			"A directory is uploaded without what its ignore file excludes, with Docker's rules: <Dockerfile>.dockerignore\n" +
			"next to the Dockerfile when it exists, else .dockerignore at the context root. The Dockerfile and the\n" +
			"ignore file are always sent. The 1 GiB context limit counts what is uploaded.\n" +
			imageNameRulesHelp +
			"Example:\n  daytona image build my-base --dockerfile Dockerfile --context .",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			meta, dockerfile, contextFile, err := buildImageMultipartMeta(cmd, a, args[0], dockerfilePath, contextPath)
			if err != nil {
				return err
			}
			if err := checkImageDockerfile(contextFile, dockerfile); err != nil {
				return err
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			resp, err := postImageMultipart(cmd.Context(), client, meta, dockerfile, contextFile)
			if err != nil {
				return err
			}
			return printCreatedImage(cmd.Context(), a, client, resp)
		},
	}
	cmd.Flags().StringVar(&dockerfilePath, "dockerfile", "", "path to the Dockerfile inside the context (default Dockerfile)")
	cmd.Flags().StringVar(&contextPath, "context", "", "path to the build context tar or directory")
	// Not MarkFlagRequired: both may come from --file
	// (buildImageMultipartMeta checks the merged values).
	addFileFlag(cmd, `JSON object {"name", "region_id", "cpu_arch", "dockerfile", "context"} (strings) from a file (or - for stdin); explicit flags override its fields`)
	addRegionFlag(cmd, "region to build the image in (default: the project's, then the organization's, then the platform's)")
	addWaitFlags(cmd)
	return cmd
}

func buildImageMultipartMeta(cmd *cobra.Command, a *app.App, name, dockerfilePath, contextPath string) (map[string]string, string, string, error) {
	body, err := input.Load(a.Globals.File, os.Stdin, false)
	if err != nil {
		return nil, "", "", err
	}
	for field, value := range body {
		switch field {
		case "name", "region_id", "cpu_arch", "dockerfile", "context":
			if _, ok := value.(string); !ok {
				return nil, "", "", cerr.Usage("image build field %q must be a string", field)
			}
		default:
			return nil, "", "", cerr.Usage("--file: unknown field %q (image build takes name, context, dockerfile, region_id, cpu_arch)", field)
		}
	}
	meta := map[string]string{"name": name}
	if value, ok := body["region_id"].(string); ok && value != "" {
		meta["region_id"] = value
	}
	if a.Globals.Region != "" {
		meta["region_id"] = a.Globals.Region
	}
	if value, ok := body["cpu_arch"].(string); ok && value != "" {
		meta["cpu_arch"] = value
	}
	// An explicit flag wins over --file, even an explicitly empty one (which
	// is then refused rather than replaced by the file's value).
	if cmd.Flags().Changed("dockerfile") {
		if strings.TrimSpace(dockerfilePath) == "" {
			return nil, "", "", cerr.Usage("--dockerfile is empty; omit it to use Dockerfile")
		}
	} else if value, ok := body["dockerfile"].(string); ok && strings.TrimSpace(value) != "" {
		dockerfilePath = value
	}
	if !cmd.Flags().Changed("context") {
		if value, ok := body["context"].(string); ok {
			contextPath = value
		}
	}
	if dockerfilePath == "" {
		dockerfilePath = "Dockerfile"
	}
	if strings.TrimSpace(contextPath) == "" {
		return nil, "", "", cerr.Usage("--context is required")
	}
	return meta, dockerfilePath, contextPath, nil
}

func archiveUploadName(contextPath string) string {
	base := filepath.Base(filepath.Clean(contextPath))
	if base == "." || base == string(filepath.Separator) || base == "" {
		return "context.tar"
	}
	return base + ".tar"
}
