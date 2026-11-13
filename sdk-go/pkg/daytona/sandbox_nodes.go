// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	stderrors "errors"
	"fmt"
	"math"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/common"
	"github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/options"
	"github.com/daytona/clients/sdk-go/pkg/types"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/google/uuid"
)

// The initial multi-node release supports whole physical 8-GPU nodes.
const gpuUnitsPerGangNode = 8

const rdmaUserspaceCheck = `sh -lc 'ldconfig -p 2>/dev/null | grep -q libibverbs.so.1 && find /usr/lib /lib -name "libmlx5-rdmav*.so" -print -quit 2>/dev/null | grep -q .'`
const rdmaUserspaceWarning = "WARNING: rdma-core userspace (libibverbs.so.1 and an mlx5 provider) is missing; NCCL may fall back to sockets."

// SandboxNode is a physical member, including rank zero, with its own data-plane clients.
// A retained handle keeps its identity across refreshes; use Nodes.All after a topology change.
type SandboxNode struct {
	apiclient.SandboxNode
	ID            string
	Rank          int32
	Process       *ProcessService
	FileSystem    *FileSystemService
	parent        *Sandbox
	toolboxClient *toolbox.APIClient
	warningsMu    sync.Mutex
	warnings      []string
}

// Warnings returns advisory RDMA and session-cleanup warnings without failing successful work.
func (n *SandboxNode) Warnings() []string {
	n.warningsMu.Lock()
	defer n.warningsMu.Unlock()
	return append([]string(nil), n.warnings...)
}

func (n *SandboxNode) warn(message string) {
	n.warningsMu.Lock()
	defer n.warningsMu.Unlock()
	for _, existing := range n.warnings {
		if existing == message {
			return
		}
	}
	n.warnings = append(n.warnings, message)
}

func (n *SandboxNode) updateProxy(proxy string) {
	cfg := n.toolboxClient.GetConfig()
	base := strings.TrimRight(proxy, "/") + "/" + url.PathEscape(n.ID)
	if len(cfg.Servers) == 1 && cfg.Servers[0].URL == base {
		return
	}
	cfg.Host, cfg.Scheme = common.ExtractHost(base), common.ExtractScheme(base)
	cfg.Servers = toolbox.ServerConfigurations{{URL: base}}
}

// GetPreviewLink obtains a preview for this member, rather than the gang head.
func (n *SandboxNode) GetPreviewLink(ctx context.Context, port int) (*types.PreviewLink, error) {
	response, httpResponse, err := n.parent.client.apiClient.SandboxAPI.GetPortPreviewUrl(n.parent.client.getAuthContext(ctx), n.ID, float32(port)).Execute()
	if err != nil {
		return nil, errors.ConvertAPIError(err, httpResponse)
	}
	return &types.PreviewLink{URL: response.GetUrl(), Token: response.GetToken()}, nil
}

// CreateSSHAccess creates SSH access for this member. A nil expiry uses the server default.
func (n *SandboxNode) CreateSSHAccess(ctx context.Context, expiresInMinutes *int32) (*apiclient.SshAccessDto, error) {
	request := n.parent.client.apiClient.SandboxAPI.CreateSshAccess(n.parent.client.getAuthContext(ctx), n.ID)
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
	_, response, err := n.parent.client.apiClient.SandboxAPI.RevokeSshAccess(n.parent.client.getAuthContext(ctx), n.ID).Token(token).Execute()
	if err != nil {
		return errors.ConvertAPIError(err, response)
	}
	return nil
}

// NodeResources specifies the resources for one appended node.
type NodeResources struct{ CPU, Memory, Disk int32 }

// SandboxNodes is a live, rank-ordered collection. Its aggregate helpers snapshot membership per call.
type SandboxNodes struct {
	parent     *Sandbox
	nodes      []*SandboxNode
	err        error
	Process    *NodeProcessService
	FileSystem *NodeFileSystemService
}

// All returns a copy of the current rank-ordered handles.
func (c *SandboxNodes) All() []*SandboxNode {
	c.parent.mu.RLock()
	defer c.parent.mu.RUnlock()
	return append([]*SandboxNode(nil), c.nodes...)
}

// Err reports invalid node metadata received from the API.
func (c *SandboxNodes) Err() error {
	c.parent.mu.RLock()
	defer c.parent.mu.RUnlock()
	return c.err
}

// Get returns the handle for a logical rank.
func (c *SandboxNodes) Get(rank int) (*SandboxNode, error) {
	c.parent.mu.RLock()
	defer c.parent.mu.RUnlock()
	if c.err != nil {
		return nil, c.err
	}
	if rank < 0 || rank >= len(c.nodes) {
		return nil, errors.NewDaytonaError(fmt.Sprintf("Sandbox node rank %d is unavailable", rank), 404, nil)
	}
	return c.nodes[rank], nil
}

// syncNodes is called with the parent lock held, except during construction.
func (s *Sandbox) syncNodes(dto sandboxDTO) {
	if s.Nodes == nil {
		s.Nodes = &SandboxNodes{parent: s}
		s.Nodes.Process = &NodeProcessService{nodes: s.Nodes}
		s.Nodes.FileSystem = &NodeFileSystemService{nodes: s.Nodes}
	}
	full, authoritative := dto.(*apiclient.Sandbox)
	if !authoritative && len(s.Nodes.nodes) > 0 {
		for _, node := range s.Nodes.nodes {
			node.updateProxy(s.ToolboxProxyUrl)
		}
		return
	}
	var raw []apiclient.SandboxNode
	if authoritative {
		if full.NodeCount != nil {
			s.NodeCount = int(full.GetNodeCount())
		}
		if s.NodeCount == 0 {
			s.NodeCount = 1
		}
		if full.TopologyGeneration != nil {
			s.TopologyGeneration = full.TopologyGeneration
		}
		if full.Degraded != nil {
			s.Degraded = full.GetDegraded()
		}
		raw = full.Nodes
	}
	if len(raw) == 0 {
		if len(s.Nodes.nodes) > 0 {
			for _, node := range s.Nodes.nodes {
				node.updateProxy(s.ToolboxProxyUrl)
			}
			return
		}
		count := s.NodeCount
		if count < 1 {
			count = 1
			s.NodeCount = count
		}
		raw = []apiclient.SandboxNode{{Rank: 0, Id: s.ID, State: dto.GetState(), Incarnation: 1, Cpu: s.Cpu / int32(count), Gpu: s.Gpu / int32(count), Memory: s.Memory / int32(count), Disk: s.Disk / int32(count)}}
	}
	seen := make(map[string]bool, len(raw))
	for rank, node := range raw {
		if node.Rank != int32(rank) || node.Id == "" || node.Id == "." || node.Id == ".." || seen[node.Id] || (rank == 0 && node.Id != s.ID) {
			s.Nodes.err = errors.NewDaytonaError("Invalid sandbox node ordering or identity", 0, nil)
			return
		}
		seen[node.Id] = true
	}
	existing := make(map[string]*SandboxNode, len(s.Nodes.nodes))
	for _, node := range s.Nodes.nodes {
		existing[node.ID] = node
	}
	for _, data := range raw {
		if node := existing[data.Id]; node != nil && node.Rank != data.Rank {
			s.Nodes.err = errors.NewDaytonaError("Sandbox node rank changed for an existing identity", 0, nil)
			return
		}
	}
	nodes := make([]*SandboxNode, len(raw))
	for rank, data := range raw {
		node := existing[data.Id]
		if node == nil {
			cfg := toolbox.NewConfiguration()
			if s.ToolboxClient != nil {
				*cfg = *s.ToolboxClient.GetConfig()
			}
			cfg.DefaultHeader = make(map[string]string, len(cfg.DefaultHeader))
			if s.ToolboxClient != nil {
				for key, value := range s.ToolboxClient.GetConfig().DefaultHeader {
					cfg.DefaultHeader[key] = value
				}
			}
			node = &SandboxNode{ID: data.Id, Rank: data.Rank, parent: s, toolboxClient: toolbox.NewAPIClient(cfg)}
			language := types.CodeLanguage(s.Labels[types.CodeToolboxLanguageLabel])
			node.Process = NewProcessService(node.toolboxClient, s.otel, language)
			node.FileSystem = NewFileSystemService(node.toolboxClient, s.otel)
		}
		node.SandboxNode = data
		node.updateProxy(s.ToolboxProxyUrl)
		nodes[rank] = node
	}
	s.Nodes.nodes, s.Nodes.err = nodes, nil
}

func nodeOperationContext(ctx context.Context, timeout []time.Duration) (context.Context, context.CancelFunc, error) {
	if len(timeout) > 1 || (len(timeout) == 1 && timeout[0] < 0) {
		return nil, nil, errors.NewDaytonaError("Node operation timeout must be non-negative", 0, nil)
	}
	limit := 10 * time.Minute
	if len(timeout) == 1 {
		limit = timeout[0]
	}
	if limit == 0 {
		return ctx, func() {}, nil
	}
	bounded, cancel := context.WithTimeout(ctx, limit)
	return bounded, cancel, nil
}

func (c *SandboxNodes) generation(ctx context.Context) (int32, error) {
	if err := c.parent.doRefreshData(ctx); err != nil {
		return 0, err
	}
	c.parent.mu.RLock()
	defer c.parent.mu.RUnlock()
	if c.parent.TopologyGeneration == nil {
		return 0, errors.NewDaytonaError("Sandbox does not expose a topology generation; elastic node operations are unavailable", 0, nil)
	}
	return *c.parent.TopologyGeneration, ctx.Err()
}

func (c *SandboxNodes) wait(ctx context.Context, operation *apiclient.GangMembershipOperation) (*apiclient.GangMembershipOperation, error) {
	for operation.Status != "COMPLETED" && operation.Status != "FAILED" {
		if err := waitNodePoll(ctx, time.Second); err != nil {
			return nil, err
		}
		updated, response, err := c.parent.client.apiClient.SandboxAPI.GetGangMembershipOperation(c.parent.client.getAuthContext(ctx), c.parent.ID, operation.Id).Execute()
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, errors.ConvertAPIError(err, response)
		}
		operation = updated
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if operation.Status == "FAILED" {
		return nil, errors.NewDaytonaError(operation.GetErrorReason(), 0, nil)
	}
	if err := c.parent.doRefreshData(ctx); err != nil {
		return nil, err
	}
	return operation, ctx.Err()
}

// Add appends one node per resource specification and waits for the operation.
// Timeout defaults to ten minutes; zero disables that limit. Cancelling a wait does not roll back the operation.
func (c *SandboxNodes) Add(ctx context.Context, nodes []NodeResources, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if len(nodes) == 0 {
		return nil, errors.NewDaytonaError("At least one node resource specification is required", 0, nil)
	}
	resources := make([]apiclient.GangNodeResources, len(nodes))
	for i, n := range nodes {
		if n.CPU <= 0 || n.Memory <= 0 || n.Disk <= 0 {
			return nil, errors.NewDaytonaError("Node CPU, memory and disk must be positive", 0, nil)
		}
		resources[i] = *apiclient.NewGangNodeResources(n.CPU, n.Memory, n.Disk)
	}
	ctx, cancel, err := nodeOperationContext(ctx, timeout)
	if err != nil {
		return nil, err
	}
	defer cancel()
	generation, err := c.generation(ctx)
	if err != nil {
		return nil, err
	}
	operation, response, err := c.parent.client.apiClient.SandboxAPI.AddSandboxNodes(c.parent.client.getAuthContext(ctx), c.parent.ID).AddGangNodes(apiclient.AddGangNodes{ExpectedGeneration: generation, Nodes: resources}).Execute()
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.ConvertAPIError(err, response)
	}
	return c.wait(ctx, operation)
}

// Release removes count highest ranks and waits for the operation.
func (c *SandboxNodes) Release(ctx context.Context, count int, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if count < 1 || count > math.MaxInt32 {
		return nil, errors.NewDaytonaError("Node release count must be positive", 0, nil)
	}
	ctx, cancel, err := nodeOperationContext(ctx, timeout)
	if err != nil {
		return nil, err
	}
	defer cancel()
	generation, err := c.generation(ctx)
	if err != nil {
		return nil, err
	}
	op, response, err := c.parent.client.apiClient.SandboxAPI.RemoveSandboxNodes(c.parent.client.getAuthContext(ctx), c.parent.ID).RemoveGangNodes(apiclient.RemoveGangNodes{ExpectedGeneration: generation, Count: int32(count)}).Execute()
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.ConvertAPIError(err, response)
	}
	return c.wait(ctx, op)
}

// Replace replaces a degraded rank and waits for the operation.
func (c *SandboxNodes) Replace(ctx context.Context, rank int, timeout ...time.Duration) (*apiclient.GangMembershipOperation, error) {
	if rank < 0 {
		return nil, errors.NewDaytonaError("Node rank must be non-negative", 0, nil)
	}
	ctx, cancel, err := nodeOperationContext(ctx, timeout)
	if err != nil {
		return nil, err
	}
	defer cancel()
	generation, err := c.generation(ctx)
	if err != nil {
		return nil, err
	}
	op, response, err := c.parent.client.apiClient.SandboxAPI.ReplaceSandboxNode(c.parent.client.getAuthContext(ctx), c.parent.ID, fmt.Sprint(rank)).ReplaceGangNode(apiclient.ReplaceGangNode{ExpectedGeneration: generation}).Execute()
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.ConvertAPIError(err, response)
	}
	return c.wait(ctx, op)
}

func waitNodePoll(ctx context.Context, interval time.Duration) error {
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// NodeErrors reports every failed rank; successful results/uploads are retained.
type NodeErrors struct{ Failures map[int32]error }

func (e *NodeErrors) Error() string {
	ranks := make([]int, 0, len(e.Failures))
	for rank := range e.Failures {
		ranks = append(ranks, int(rank))
	}
	sort.Ints(ranks)
	parts := make([]string, 0, len(ranks))
	for _, rank := range ranks {
		parts = append(parts, fmt.Sprintf("rank %d: %v", rank, e.Failures[int32(rank)]))
	}
	return "Sandbox node operations failed: " + strings.Join(parts, "; ")
}
func (e *NodeErrors) Unwrap() []error {
	result := make([]error, 0, len(e.Failures))
	for _, err := range e.Failures {
		result = append(result, err)
	}
	return result
}

// NodeExecuteOptions controls per-rank execution. Timeout zero leaves execution bounded by ctx.
// Callbacks receive rank separately, are serialized, and finish before ExecuteCommand returns.
type NodeExecuteOptions struct {
	Timeout            time.Duration
	OnStdout, OnStderr func(rank int32, chunk string)
}
type NodeProcessService struct{ nodes *SandboxNodes }
type NodeFileSystemService struct{ nodes *SandboxNodes }

// ExecuteCommand executes concurrently on a membership snapshot. Each rank gets a private session,
// destroyed on success, failure or cancellation. Cleanup requires a reachable API.
func (p *NodeProcessService) ExecuteCommand(ctx context.Context, command string, options ...NodeExecuteOptions) (map[int32]*types.ExecuteResponse, error) {
	if len(options) > 1 || (len(options) == 1 && options[0].Timeout < 0) {
		return nil, errors.NewDaytonaError("Execution timeout must be non-negative", 0, nil)
	}
	if err := p.nodes.Err(); err != nil {
		return nil, err
	}
	var opts NodeExecuteOptions
	if len(options) > 0 {
		opts = options[0]
	}
	nodes := p.nodes.All()
	results := make(map[int32]*types.ExecuteResponse, len(nodes))
	failures := make(map[int32]error)
	var mu, callbacks sync.Mutex
	var group sync.WaitGroup
	for _, node := range nodes {
		group.Add(1)
		go func(node *SandboxNode) {
			defer group.Done()
			result, err := executeNode(ctx, node, command, opts, len(nodes) > 1, &callbacks)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				failures[node.Rank] = err
			} else {
				results[node.Rank] = result
			}
		}(node)
	}
	group.Wait()
	if len(failures) > 0 {
		return results, &NodeErrors{Failures: failures}
	}
	return results, nil
}

func executeNode(ctx context.Context, node *SandboxNode, command string, opts NodeExecuteOptions, checkRDMA bool, callbacks *sync.Mutex) (result *types.ExecuteResponse, err error) {
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	warning := ""
	if checkRDMA {
		probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		probe, probeErr := node.Process.ExecuteCommand(probeCtx, rdmaUserspaceCheck, options.WithExecuteTimeout(5*time.Second))
		cancel()
		if probeErr != nil {
			warning = "WARNING: Could not check RDMA userspace; continuing without verification."
		} else if probe.ExitCode != 0 {
			warning = rdmaUserspaceWarning
		}
		if warning != "" {
			node.warn(warning)
			if opts.OnStderr != nil {
				callbacks.Lock()
				opts.OnStderr(node.Rank, warning+"\n")
				callbacks.Unlock()
			}
		}
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	session := "daytona-nodes-exec-" + uuid.NewString()
	defer func() {
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer cancel()
		if cleanupErr := node.Process.DeleteSession(cleanupCtx, session); cleanupErr != nil {
			if err != nil {
				err = stderrors.Join(err, cleanupErr)
			} else {
				node.warn(fmt.Sprintf("Session %s cleanup failed: %v", session, cleanupErr))
			}
		}
	}()
	setupCtx, cancelSetup := context.WithTimeout(ctx, 10*time.Second)
	err = node.Process.CreateSession(setupCtx, session)
	cancelSetup()
	if err != nil {
		return nil, err
	}
	execCtx, cancel := context.WithCancel(ctx)
	if opts.Timeout > 0 {
		cancel()
		execCtx, cancel = context.WithTimeout(ctx, opts.Timeout)
	}
	defer cancel()
	started, err := node.Process.ExecuteSessionCommand(execCtx, session, command, true, false)
	if err != nil {
		if execCtx.Err() != nil {
			return nil, execCtx.Err()
		}
		return nil, err
	}
	commandID, ok := started["id"].(string)
	if !ok || commandID == "" {
		return nil, errors.NewDaytonaError("Session execution returned no command ID", 0, nil)
	}
	if opts.OnStdout != nil || opts.OnStderr != nil {
		stdout, stderr := make(chan string, 16), make(chan string, 16)
		streamDone := make(chan error, 1)
		go func() {
			streamDone <- node.Process.GetSessionCommandLogsStream(execCtx, session, commandID, stdout, stderr)
		}()
		for stdout != nil || stderr != nil {
			select {
			case chunk, open := <-stdout:
				if !open {
					stdout = nil
				} else if opts.OnStdout != nil && execCtx.Err() == nil {
					callbacks.Lock()
					opts.OnStdout(node.Rank, chunk)
					callbacks.Unlock()
				}
			case chunk, open := <-stderr:
				if !open {
					stderr = nil
				} else if opts.OnStderr != nil && execCtx.Err() == nil {
					callbacks.Lock()
					opts.OnStderr(node.Rank, chunk)
					callbacks.Unlock()
				}
			}
		}
		if streamErr := <-streamDone; streamErr != nil {
			if execCtx.Err() != nil {
				return nil, execCtx.Err()
			}
			return nil, streamErr
		}
	}
	var exitCode int
	for {
		status, statusErr := node.Process.GetSessionCommand(execCtx, session, commandID)
		if statusErr != nil {
			if execCtx.Err() != nil {
				return nil, execCtx.Err()
			}
			return nil, statusErr
		}
		if value, done := status["exitCode"]; done {
			switch code := value.(type) {
			case int32:
				exitCode = int(code)
			case int:
				exitCode = code
			default:
				return nil, errors.NewDaytonaError("Session command returned an invalid exit code", 0, nil)
			}
			break
		}
		if err = waitNodePoll(execCtx, 100*time.Millisecond); err != nil {
			return nil, err
		}
	}
	logs, err := node.Process.GetSessionCommandLogs(execCtx, session, commandID)
	if err != nil {
		if execCtx.Err() != nil {
			return nil, execCtx.Err()
		}
		return nil, err
	}
	stdout := logs.GetStdout()
	output := logs.GetOutput()
	if output == "" {
		output = stdout + logs.GetStderr()
	}
	if warning != "" {
		output = warning + "\n" + output
		stdout = warning + "\n" + stdout
	}
	return &types.ExecuteResponse{ExitCode: exitCode, Result: output, Artifacts: &types.ExecutionArtifacts{Stdout: stdout}}, execCtx.Err()
}

// UploadFile uploads a path or byte slice to every current node concurrently, without rollback.
func (f *NodeFileSystemService) UploadFile(ctx context.Context, source any, destination string) error {
	if err := f.nodes.Err(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if path, ok := source.(string); ok {
		// Share the bytes rather than reading a large local file once per rank.
		data, err := os.ReadFile(path)
		if err != nil {
			return errors.WrapDaytonaError(err, "Failed to read upload file", 0, nil)
		}
		source = data
	}
	failures := make(map[int32]error)
	var mu sync.Mutex
	var group sync.WaitGroup
	for _, node := range f.nodes.All() {
		group.Add(1)
		go func(node *SandboxNode) {
			defer group.Done()
			if err := node.FileSystem.UploadFile(ctx, source, destination); err != nil {
				mu.Lock()
				failures[node.Rank] = err
				mu.Unlock()
			}
		}(node)
	}
	group.Wait()
	if len(failures) > 0 {
		return &NodeErrors{Failures: failures}
	}
	return nil
}

func applyPerNodeResources(request *apiclient.CreateSandbox, resources *types.Resources) error {
	convert := func(name string, scalar int, values []int) ([]int32, error) {
		if values == nil {
			return nil, nil
		}
		if len(values) == 0 || scalar != 0 {
			return nil, errors.NewDaytonaError(name+" must use either a scalar or a non-empty per-node array", 0, nil)
		}
		converted := make([]int32, len(values))
		for i, value := range values {
			if value < 1 || value > math.MaxInt32 {
				return nil, errors.NewDaytonaError(name+" per-node values must be positive 32-bit integers", 0, nil)
			}
			converted[i] = int32(value)
		}
		return converted, nil
	}
	cpu, err := convert("cpu", resources.CPU, resources.CPUPerNode)
	if err != nil {
		return err
	}
	memory, err := convert("memory", resources.Memory, resources.MemoryPerNode)
	if err != nil {
		return err
	}
	disk, err := convert("disk", resources.Disk, resources.DiskPerNode)
	if err != nil {
		return err
	}
	if cpu != nil {
		request.SetCpu(apiclient.ArrayOfInt32AsCreateSandboxCpu(&cpu))
	}
	if memory != nil {
		request.SetMemory(apiclient.ArrayOfInt32AsCreateSandboxMemory(&memory))
	}
	if disk != nil {
		request.SetDisk(apiclient.ArrayOfInt32AsCreateSandboxDisk(&disk))
	}
	return nil
}
