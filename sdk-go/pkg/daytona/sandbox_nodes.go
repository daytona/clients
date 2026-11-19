// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/options"
	"github.com/daytona/clients/sdk-go/pkg/types"
)

const gpuUnitsPerNode = 8

const rdmaUserspaceCheck = `sh -lc 'ldconfig -p 2>/dev/null | grep -q libibverbs.so.1 && find /usr/lib /lib -name "libmlx5-rdmav*.so" -print -quit 2>/dev/null | grep -q .'`
const rdmaUserspaceWarning = "WARNING: rdma-core userspace (libibverbs.so.1 and an mlx5 provider) is missing; NCCL may fall back to sockets."

// SandboxNode describes a physical rank and its data-plane services.
type SandboxNode struct {
	apiclient.SandboxNode
	Process    *ProcessService
	FileSystem *FileSystemService
	client     *Client
	rdma       *nodeRdmaProbe
}

type nodeRdmaProbe struct {
	incarnation int32
	started     atomic.Bool
	warning     atomic.Pointer[string]
}

// Warnings returns advisory messages from all-node execution.
func (n *SandboxNode) Warnings() []string {
	if n.rdma != nil {
		if warning := n.rdma.warning.Load(); warning != nil {
			return []string{*warning}
		}
	}
	return nil
}

// SandboxNodes is a point-in-time membership view. Fetch it again with
// [Sandbox.Nodes] after a topology change. Mutations use the generation read
// with the view so they cannot act on a different topology.
type SandboxNodes struct {
	client     *Client
	sandboxID  string
	generation int32
	members    []*SandboxNode
	Degraded   bool
}

// Nodes fetches the current membership. An ordinary sandbox has one node at
// rank zero. A direct worker lookup exposes only the addressed rank.
func (s *Sandbox) Nodes(ctx context.Context) (*SandboxNodes, error) {
	s.mu.RLock()
	id := s.ID
	s.mu.RUnlock()
	dto, resp, err := s.client.apiClient.SandboxAPI.GetSandbox(s.client.getAuthContext(ctx), id).Execute()
	if err != nil {
		return nil, errors.ConvertAPIError(err, resp)
	}
	return s.nodesFromDTO(dto)
}

func (s *Sandbox) nodesFromDTO(dto *apiclient.Sandbox) (*SandboxNodes, error) {
	var details apiclient.SandboxMembershipDetails
	if len(dto.AdditionalProperties) != 0 {
		raw, err := json.Marshal(dto.AdditionalProperties)
		if err != nil {
			return nil, fmt.Errorf("encode sandbox membership: %w", err)
		}
		if err := json.Unmarshal(raw, &details); err != nil {
			return nil, fmt.Errorf("decode sandbox membership: %w", err)
		}
	}

	firstRank := details.GetNodeRank()
	worker := details.GetHeadSandboxId() != ""
	count := details.GetNodeCount()
	if details.HasNodeCount() && count < 1 {
		return nil, errors.NewDaytonaError("Invalid sandbox node count", 0, nil)
	}
	if firstRank < 0 || (firstRank > 0) != worker || (worker && len(details.Nodes) > 1) {
		return nil, errors.NewDaytonaError("Invalid sandbox worker rank", 0, nil)
	}
	if len(details.Nodes) == 0 {
		if !worker && (count > 1 || dto.Gpu > gpuUnitsPerNode) {
			return nil, errors.NewDaytonaError("Node membership is unavailable. Fetch Nodes again.", 0, nil)
		}
		incarnation := int32(1)
		if details.Incarnation != nil {
			incarnation = *details.Incarnation
		}
		details.Nodes = []apiclient.SandboxNode{{
			Rank: firstRank, Id: dto.Id, State: dto.GetState(), Incarnation: incarnation,
			Cpu: dto.Cpu, Gpu: dto.Gpu, Memory: dto.Memory, Disk: dto.Disk,
		}}
	}
	if !worker && count > 0 && len(details.Nodes) != int(count) {
		return nil, errors.NewDaytonaError("Incomplete sandbox node membership. Fetch Nodes again.", 0, nil)
	}
	nodes := &SandboxNodes{client: s.client, sandboxID: dto.Id, Degraded: details.GetDegraded()}
	if !worker {
		nodes.generation = details.GetTopologyGeneration()
	}
	seen := make(map[string]bool, len(details.Nodes))
	for i, member := range details.Nodes {
		if member.Rank != firstRank+int32(i) || member.Incarnation < 1 || seen[member.Id] ||
			member.Id == "" || member.Id == "." || member.Id == ".." || strings.ContainsAny(member.Id, "/\\?#%") ||
			(i == 0 && member.Id != dto.Id) {
			return nil, errors.NewDaytonaError("Invalid sandbox node identity or rank", 0, nil)
		}
		seen[member.Id] = true
		tb, err := s.client.createToolboxClient(dto.ToolboxProxyUrl, member.Id)
		if err != nil {
			return nil, err
		}
		node := &SandboxNode{SandboxNode: member, client: s.client,
			Process:    NewProcessService(tb, s.otel, types.CodeLanguage(dto.Labels[types.CodeToolboxLanguageLabel])),
			FileSystem: NewFileSystemService(tb, s.otel)}
		nodes.members = append(nodes.members, node)
	}
	// Nodes returns new handles on every fetch. Retain diagnostics for surviving incarnations.
	if len(nodes.members) > 1 {
		s.mu.Lock()
		probes := make(map[string]*nodeRdmaProbe, len(nodes.members))
		for _, node := range nodes.members {
			probe := s.nodeProbes[node.Id]
			if probe == nil || probe.incarnation != node.Incarnation {
				probe = &nodeRdmaProbe{incarnation: node.Incarnation}
			}
			node.rdma = probe
			probes[node.Id] = probe
		}
		s.nodeProbes = probes
		s.mu.Unlock()
	}
	return nodes, nil
}

// All returns the members in rank order.
func (c *SandboxNodes) All() []*SandboxNode { return slices.Clone(c.members) }

// Get returns the member with the requested rank, including for a direct worker lookup.
func (c *SandboxNodes) Get(rank int) (*SandboxNode, error) {
	for _, node := range c.members {
		if int(node.Rank) == rank {
			return node, nil
		}
	}
	return nil, errors.NewDaytonaError(fmt.Sprintf("Sandbox node rank %d is unavailable", rank), 404, nil)
}

// TopologyGeneration returns the committed generation, or zero when membership mutations are unavailable.
func (c *SandboxNodes) TopologyGeneration() int32 { return c.generation }

// GetPreviewLink obtains a preview for this member.
func (n *SandboxNode) GetPreviewLink(ctx context.Context, port int) (*types.PreviewLink, error) {
	response, httpResponse, err := n.client.apiClient.SandboxAPI.GetPortPreviewUrl(n.client.getAuthContext(ctx), n.Id, float32(port)).Execute()
	if err != nil {
		return nil, errors.ConvertAPIError(err, httpResponse)
	}
	return &types.PreviewLink{URL: response.GetUrl(), Token: response.GetToken()}, nil
}

// CreateSSHAccess creates SSH access for this member. A nil expiry uses the server default.
func (n *SandboxNode) CreateSSHAccess(ctx context.Context, expiresInMinutes *int32) (*apiclient.SshAccessDto, error) {
	request := n.client.apiClient.SandboxAPI.CreateSshAccess(n.client.getAuthContext(ctx), n.Id)
	if expiresInMinutes != nil {
		request = request.ExpiresInMinutes(float32(*expiresInMinutes))
	}
	response, httpResponse, err := request.Execute()
	if err != nil {
		return nil, errors.ConvertAPIError(err, httpResponse)
	}
	return response, nil
}

// RevokeSSHAccess revokes a token for this member.
func (n *SandboxNode) RevokeSSHAccess(ctx context.Context, token string) error {
	_, response, err := n.client.apiClient.SandboxAPI.RevokeSshAccess(n.client.getAuthContext(ctx), n.Id).Token(token).Execute()
	if err != nil {
		return errors.ConvertAPIError(err, response)
	}
	return nil
}

// NodeResources is the allocation for one appended rank.
type NodeResources struct{ CPU, Memory, Disk int32 }

// Add appends one node per allocation and waits for the operation.
func (c *SandboxNodes) Add(ctx context.Context, nodes []NodeResources, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if len(nodes) == 0 {
		return nil, errors.NewDaytonaError("At least one node is required", 0, nil)
	}
	resources := make([]apiclient.GangNodeResources, len(nodes))
	for i, n := range nodes {
		if n.CPU <= 0 || n.Memory <= 0 || n.Disk <= 0 {
			return nil, errors.NewDaytonaError("Node CPU, memory and disk must be positive", 0, nil)
		}
		resources[i] = *apiclient.NewGangNodeResources(n.CPU, n.Memory, n.Disk)
	}
	return c.change(ctx, timeout, func(auth context.Context) (*apiclient.GangMembershipOperation, *http.Response, error) {
		return c.client.apiClient.SandboxMembershipAPI.AddSandboxNodes(auth, c.sandboxID).
			AddGangNodes(apiclient.AddGangNodes{ExpectedGeneration: c.generation, Nodes: resources}).Execute()
	})
}

// Release removes count highest ranks and waits for the operation.
func (c *SandboxNodes) Release(ctx context.Context, count int, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if count < 1 || count > math.MaxInt32 {
		return nil, errors.NewDaytonaError("Invalid node release count", 0, nil)
	}
	return c.change(ctx, timeout, func(auth context.Context) (*apiclient.GangMembershipOperation, *http.Response, error) {
		return c.client.apiClient.SandboxMembershipAPI.RemoveSandboxNodes(auth, c.sandboxID).
			RemoveGangNodes(apiclient.RemoveGangNodes{ExpectedGeneration: c.generation, Count: int32(count)}).Execute()
	})
}

// Replace requests replacement of a degraded rank and waits for the operation.
func (c *SandboxNodes) Replace(ctx context.Context, rank int, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if rank < 0 || rank > math.MaxInt32 {
		return nil, errors.NewDaytonaError("Invalid replacement rank", 0, nil)
	}
	return c.change(ctx, timeout, func(auth context.Context) (*apiclient.GangMembershipOperation, *http.Response, error) {
		return c.client.apiClient.SandboxMembershipAPI.ReplaceSandboxNode(auth, c.sandboxID, fmt.Sprint(rank)).
			ReplaceGangNode(apiclient.ReplaceGangNode{ExpectedGeneration: c.generation}).Execute()
	})
}

// Operation retrieves the current status of a previously accepted membership change.
func (c *SandboxNodes) Operation(ctx context.Context, id string) (*apiclient.GangMembershipOperation, error) {
	op, response, err := c.client.apiClient.SandboxMembershipAPI.GetGangMembershipOperation(c.client.getAuthContext(ctx), c.sandboxID, id).Execute()
	if err != nil {
		return nil, errors.ConvertAPIError(err, response)
	}
	return op, nil
}

func (c *SandboxNodes) change(ctx context.Context, timeout []time.Duration, submit func(context.Context) (*apiclient.GangMembershipOperation, *http.Response, error)) (*apiclient.GangMembershipOperation, error) {
	if c.generation < 1 {
		return nil, errors.NewDaytonaError("Membership changes require a multi-node sandbox head", 0, nil)
	}
	if len(timeout) > 1 || (len(timeout) == 1 && timeout[0] < 0) {
		return nil, errors.NewDaytonaError("Invalid membership timeout", 0, nil)
	}
	limit := 10 * time.Minute
	if len(timeout) == 1 {
		limit = timeout[0]
	}
	if limit > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, limit)
		defer cancel()
	}
	op, response, err := submit(c.client.getAuthContext(ctx))
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.ConvertAPIError(err, response)
	}
	for {
		switch op.Status {
		case "COMPLETED":
			return op, nil
		case "FAILED":
			return op, errors.NewDaytonaError("Membership operation failed: "+op.GetErrorReason(), 0, nil)
		}
		select {
		case <-ctx.Done():
			return op, ctx.Err()
		case <-time.After(time.Second):
		}
		current, err := c.Operation(ctx, op.Id)
		if err != nil {
			if ctx.Err() != nil {
				return op, ctx.Err()
			}
			return op, err
		}
		op = current
	}
}

func setMultiNodeResources(request *apiclient.CreateSandbox, params *types.MultiNodeImageParams) error {
	r := params.Resources
	if r == nil || r.GPU <= gpuUnitsPerNode || r.GPU > math.MaxInt32 || r.GPU%gpuUnitsPerNode != 0 {
		return errors.NewDaytonaError("Multi-node image creation requires at least two whole 8-GPU nodes", 0, nil)
	}
	if len(r.GpuType) == 0 {
		return errors.NewDaytonaError("Multi-node image creation requires an explicit GPU type", 0, nil)
	}
	for _, dimension := range []struct {
		name   string
		scalar int
		values []int32
	}{
		{"cpu", r.CPU, params.CPUPerNode},
		{"memory", r.Memory, params.MemoryPerNode},
		{"disk", r.Disk, params.DiskPerNode},
	} {
		if dimension.values == nil {
			continue
		}
		if dimension.scalar != 0 || len(dimension.values) != r.GPU/gpuUnitsPerNode {
			return errors.NewDaytonaError("Specify either scalar "+dimension.name+" or one value per rank", 0, nil)
		}
		for _, n := range dimension.values {
			if n < 1 {
				return errors.NewDaytonaError(dimension.name+" per-node values must be positive", 0, nil)
			}
		}
		if request.AdditionalProperties == nil {
			request.AdditionalProperties = make(map[string]any)
		}
		request.AdditionalProperties[dimension.name+"PerNode"] = dimension.values
	}
	return nil
}

// NodeErrors contains the errors from individual ranks. Other ranks can succeed.
type NodeErrors struct{ Failures map[int32]error }

func (e *NodeErrors) Error() string {
	ranks := make([]int, 0, len(e.Failures))
	for rank := range e.Failures {
		ranks = append(ranks, int(rank))
	}
	slices.Sort(ranks)
	parts := make([]string, 0, len(ranks))
	for _, rank := range ranks {
		parts = append(parts, fmt.Sprintf("rank %d: %v", rank, e.Failures[int32(rank)]))
	}
	return "Sandbox node operations failed: " + strings.Join(parts, "; ")
}

func (e *NodeErrors) Unwrap() []error {
	errs := make([]error, 0, len(e.Failures))
	for _, err := range e.Failures {
		errs = append(errs, err)
	}
	return errs
}

func runOnNodes[T any](members []*SandboxNode, fn func(*SandboxNode) (T, error)) (map[int32]T, error) {
	if len(members) == 0 {
		return nil, errors.NewDaytonaError("Node membership is unavailable. Fetch Nodes again.", 0, nil)
	}
	results := make(map[int32]T, len(members))
	failures := make(map[int32]error)
	var group sync.WaitGroup
	var mu sync.Mutex
	for _, n := range members {
		group.Go(func() {
			result, err := fn(n)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				failures[n.Rank] = err
			} else {
				results[n.Rank] = result
			}
		})
	}
	group.Wait()
	if len(failures) > 0 {
		return results, &NodeErrors{Failures: failures}
	}
	return results, nil
}

// ExecuteCommand runs a buffered command concurrently on each member. Results
// are keyed by rank, including successful ranks when another rank fails.
// For live output, use a session on the individual node's Process service.
func (c *SandboxNodes) ExecuteCommand(ctx context.Context, command string, timeout ...time.Duration) (map[int32]*types.ExecuteResponse, error) {
	if len(timeout) > 1 || (len(timeout) == 1 && timeout[0] < 0) {
		return nil, errors.NewDaytonaError("Invalid execution timeout", 0, nil)
	}
	var opts []func(*options.ExecuteCommand)
	if len(timeout) == 1 {
		opts = append(opts, options.WithExecuteTimeout(timeout[0]))
	}
	return runOnNodes(c.members, func(n *SandboxNode) (*types.ExecuteResponse, error) {
		if n.rdma != nil && n.rdma.started.CompareAndSwap(false, true) {
			probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			probe, err := n.Process.ExecuteCommand(probeCtx, rdmaUserspaceCheck, options.WithExecuteTimeout(5*time.Second))
			cancel()
			if ctx.Err() != nil {
				n.rdma.started.Store(false)
				return nil, ctx.Err()
			}
			var warning string
			if err != nil {
				warning = "Could not check RDMA userspace; continuing without verification."
			} else if probe.ExitCode != 0 {
				warning = rdmaUserspaceWarning
			}
			if warning != "" {
				n.rdma.warning.Store(&warning)
			}
		}
		return n.Process.ExecuteCommand(ctx, command, opts...)
	})
}

// UploadFile streams a local file or byte slice to every member. Invalid sources
// return a single error. A [NodeErrors] result identifies failed uploads by rank.
func (c *SandboxNodes) UploadFile(ctx context.Context, source any, destination string) error {
	var upload func(*SandboxNode) error
	switch value := source.(type) {
	case string:
		flags := os.O_RDONLY
		if len(c.members) > 1 {
			flags |= nodeUploadNonblock
		}
		file, err := os.OpenFile(value, flags, 0)
		if err != nil {
			return err
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil {
			return err
		}
		if info.IsDir() {
			return errors.NewDaytonaError("Upload source is a directory", 0, nil)
		}
		if len(c.members) > 1 && info.Mode()&os.ModeNamedPipe != 0 {
			return errors.NewDaytonaError("multi-node uploads require a seekable file or byte slice", 0, nil)
		}
		size := info.Size()
		if size == 0 || !info.Mode().IsRegular() {
			// Some virtual files report zero even when they have readable contents.
			var probe [1]byte
			n, err := file.ReadAt(probe[:], 0)
			if err != nil && err != io.EOF {
				if len(c.members) == 1 {
					upload = func(n *SandboxNode) error {
						return n.FileSystem.UploadFileStream(ctx, file, destination)
					}
					break
				}
				return errors.WrapDaytonaError(err, fmt.Sprintf("multi-node uploads require a seekable file or byte slice: %v", err), 0, nil)
			}
			if n > 0 && size == 0 {
				size = math.MaxInt64
			}
		}
		upload = func(n *SandboxNode) error {
			// Each rank reads the validated file with its own offset.
			return n.FileSystem.UploadFileStream(ctx, io.NewSectionReader(file, 0, size), destination)
		}
	case []byte:
		upload = func(n *SandboxNode) error {
			return n.FileSystem.UploadFileStream(ctx, bytes.NewReader(value), destination)
		}
	default:
		return errors.NewDaytonaError("Upload source must be a local path or byte slice", 0, nil)
	}
	_, err := runOnNodes(c.members, func(n *SandboxNode) (struct{}, error) {
		return struct{}{}, upload(n)
	})
	return err
}
