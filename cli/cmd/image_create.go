// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

func newImageImportCommand() *cobra.Command {
	ref := ""
	cmd := &cobra.Command{
		Use:     "create <name>",
		Aliases: []string{"import"},
		Short:   "Import an image from a registry ref",
		Long:    "Import an image from a registry ref.\nExample:\n  daytona image create my-base --ref python:3.12",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			body, err := input.Load(a.Globals.File, os.Stdin, false)
			if err != nil {
				return err
			}
			body.Set("name", args[0])
			if a.Globals.Region != "" {
				body.Set("region_id", a.Globals.Region)
			}
			workload.SetIfChanged(body, cmd.Flags(), "ref", "source", map[string]any{"type": string(gen.PublicImageSource0TypeRegistry), "ref": ref})
			source, ok := body["source"]
			if !ok || source == nil {
				return cerr.Usage("--ref is required")
			}
			req, err := workload.DecodeBody[gen.CreateImageRequest](body)
			if err != nil {
				return err
			}
			client, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
			if err != nil {
				return err
			}
			var created gen.AsyncOperation
			if err := client.Do(cmd.Context(), http.MethodPost, "/images", nil, req, &created); err != nil {
				return err
			}
			return printCreatedImage(cmd.Context(), a, client, created)
		},
	}
	cmd.Flags().StringVar(&ref, "ref", "", "registry ref to import")
	// Not MarkFlagRequired: `source` may come from --file (the RunE checks
	// the merged body).
	return cmd
}

func newImageBuildCommand() *cobra.Command {
	dockerfilePath := ""
	contextPath := ""
	cmd := &cobra.Command{
		Use:   "build <name>",
		Short: "Build an image from a Dockerfile context",
		Long:  "Build an image from a Dockerfile context.\nExample:\n  daytona image build my-base --dockerfile Dockerfile --context .",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			meta, dockerfile, contextFile, err := buildImageMultipartMeta(cmd, a, args[0], dockerfilePath, contextPath)
			if err != nil {
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
	cmd.Flags().StringVar(&dockerfilePath, "dockerfile", "", "path to the Dockerfile inside the context")
	cmd.Flags().StringVar(&contextPath, "context", "", "path to the build context tar or directory")
	// Not MarkFlagRequired: both may come from --file
	// (buildImageMultipartMeta checks the merged values).
	return cmd
}

func buildImageMultipartMeta(cmd *cobra.Command, a *app.App, name, dockerfilePath, contextPath string) (map[string]string, string, string, error) {
	body, err := input.Load(a.Globals.File, os.Stdin, false)
	if err != nil {
		return nil, "", "", err
	}
	for field := range body {
		switch field {
		case "name", "region_id", "cpu_arch", "dockerfile", "context":
		default:
			return nil, "", "", cerr.Usage("unknown image build field %q", field)
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
	if value, ok := body["dockerfile"].(string); ok && value != "" && dockerfilePath == "" {
		dockerfilePath = value
	}
	if value, ok := body["context"].(string); ok && value != "" && contextPath == "" {
		contextPath = value
	}
	if dockerfilePath == "" {
		dockerfilePath = "Dockerfile"
	}
	if contextPath == "" {
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
