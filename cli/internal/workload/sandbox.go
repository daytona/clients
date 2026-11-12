// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/regions"
	"golang.org/x/sync/errgroup"
)

const (
	ClassLinux = "linux"
	ClassMacOS = "macos"
)

type ResolvedSandbox struct {
	Client *api.Client
	Detail gen.Sandbox
	// Raw is the API's JSON for Detail, which is what `-o json` prints.
	Raw api.Raw[gen.Sandbox]
}

func ResolveSandbox(ctx context.Context, home *api.Client, cache *CatalogCache, id string) (ResolvedSandbox, error) {
	if tag := regions.TagOf(id); tag != "" {
		cat, err := cache.Fetch(ctx, home)
		if err != nil {
			return ResolvedSandbox{}, err
		}
		if tag = regions.TagOf(id, cat); tag != "" {
			return fetchSandbox(ctx, regions.ClientFor(ctx, home, cat, id), id)
		}
	}

	resolved, err := fetchSandbox(ctx, home, id)
	if err == nil || !isNotFound(err) {
		return resolved, err
	}

	cat, catErr := cache.Fetch(ctx, home)
	if catErr != nil {
		// Not found at home is not conclusive: the sandbox may live in a
		// region the catalog would have named.
		catalogErr := &cerr.Error{
			Code: cerr.ExitCode(catErr),
			Msg:  fmt.Sprintf("sandbox %s is not in the home region, and the region catalog could not be read to search the others: %v", id, catErr),
			Err:  errors.Join(catErr, err),
		}
		var cause *cerr.Error
		if errors.As(catErr, &cause) {
			catalogErr.Hint = cause.Hint
		}
		return ResolvedSandbox{}, catalogErr
	}
	if cat == nil {
		return ResolvedSandbox{}, err
	}

	seen := map[string]struct{}{home.BaseURL: {}}
	listedRegions := cat.OrderedRegions()
	bases := make([]string, 0, len(listedRegions))
	for _, region := range listedRegions {
		base := regions.BaseFor(region, home.BaseURL)
		if _, ok := seen[base]; ok {
			continue
		}
		seen[base] = struct{}{}
		bases = append(bases, base)
	}
	if len(bases) == 0 {
		return ResolvedSandbox{}, err
	}

	probeCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	var (
		mu      sync.Mutex
		matches []ResolvedSandbox
		hardErr error
	)
	g, gctx := errgroup.WithContext(probeCtx)
	for _, base := range bases {
		g.Go(func() error {
			candidate, candidateErr := fetchSandbox(gctx, home.WithBaseURL(base), id)
			if candidateErr == nil {
				mu.Lock()
				matches = append(matches, candidate)
				ambiguous := len(matches) > 1
				mu.Unlock()
				if ambiguous {
					cancel()
				}
				return nil
			}
			if isNotFound(candidateErr) || errors.Is(candidateErr, context.Canceled) {
				return nil
			}
			mu.Lock()
			if hardErr == nil {
				hardErr = candidateErr
			}
			mu.Unlock()
			cancel()
			return candidateErr
		})
	}
	_ = g.Wait()
	// Probes cancelled by probeCtx (a hard error, a second match) are ignored
	// above; the caller's own cancellation must surface.
	if callerErr := cerr.FromContext(ctx); callerErr != nil {
		return ResolvedSandbox{}, callerErr
	}
	if hardErr != nil {
		return ResolvedSandbox{}, hardErr
	}
	if len(matches) > 1 {
		return ResolvedSandbox{}, ambiguousSandboxError(id, matches)
	}
	if len(matches) == 1 {
		return matches[0], nil
	}
	return ResolvedSandbox{}, err
}

func LinuxOnlyError(action, id, class string) error {
	return cerr.New(cerr.ExitConflict, "%s is linux-only; sandbox %s is %s", action, id, class)
}

func fetchSandbox(ctx context.Context, client *api.Client, id string) (ResolvedSandbox, error) {
	var detail api.Raw[gen.Sandbox]
	if err := client.Do(ctx, http.MethodGet, fmt.Sprintf("/sandboxes/%s", url.PathEscape(id)), nil, nil, &detail); err != nil {
		return ResolvedSandbox{}, err
	}
	return ResolvedSandbox{Client: client, Detail: detail.V, Raw: detail}, nil
}

func isNotFound(err error) bool {
	var apiErr *api.APIError
	return errors.As(err, &apiErr) && apiErr.Status == http.StatusNotFound
}

func ambiguousSandboxError(id string, matches []ResolvedSandbox) error {
	regionsFound := make([]string, 0, len(matches))
	for _, match := range matches {
		regionsFound = append(regionsFound, match.Detail.RegionId)
	}
	sort.Strings(regionsFound)
	return cerr.Usage("sandbox %q is ambiguous across regions (%s)", id, strings.Join(regionsFound, ", ")).
		WithHint("pass the region-tagged sandbox id instead")
}
