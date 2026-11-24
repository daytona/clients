// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// Only cross-function forwarding and post-initialization changes need explicit
// descriptions. Keys pin the AST expression as well as its method: a new or
// misspelled production Path never silently inherits an old route-table entry.
var routeForwarders = map[string][]sdkRoute{
	// Discovered build-log destinations are signed URLs, not hard-coded routes.
	"build_logs.go|GET|target.path":          nil,
	"build_logs_follow.go|GET|f.target.path": nil,
	"build_logs_request.go|GET|path": apiRouteUses("GET",
		"/images/{param}/build-logs-url", "/sandboxes/{param}/build-logs-url"),
	"create_submit.go|POST|prepared.path": apiRouteUses("POST",
		"/sandboxes", "/sandboxes/from-snapshot", "/sandboxes/macos"),
	"image_list.go|GET|path": apiRouteUses("GET",
		"/images", "/images/search", "/snapshots", "/snapshots/search"),
	"image_wait.go|GET|":              apiRouteUses("GET", "/images/{param}", "/snapshots/{param}"),
	"images_ready.go|GET|":            apiRouteUses("GET", "/images/{param}"),
	"images_build.go|GET|":            nil, // Request options populated before POST; assignment inventoried below.
	"sandbox_wait_read.go|GET|":       nil, // Zero-valued requests returned with errors, never sent.
	"sandbox_wait_read.go|GET|w.path": apiRouteUses("GET", "/sandboxes/{param}"),
	"sandbox_list.go|GET|path":        apiRouteUses("GET", "/sandboxes", "/sandboxes/search"),
	"sandbox_lifecycle.go|POST|w.path + \"/\" + transition.action": apiRouteUses("POST",
		"/sandboxes/{param}/stop", "/sandboxes/{param}/pause", "/sandboxes/{param}/resume"),
	"sandbox_lifecycle_start.go|POST|w.path + \"/\" + action": apiRouteUses("POST",
		"/sandboxes/{param}/start", "/sandboxes/{param}/resume"),
	"sandbox_network.go|GET|":                       apiRouteUses("GET", "/sandboxes/{param}/network-policy"),
	"sandbox_network.go|PUT|":                       apiRouteUses("PUT", "/sandboxes/{param}/network-policy"),
	"sandbox_secrets.go|GET|":                       apiRouteUses("GET", "/sandboxes/{param}/secrets"),
	"sandbox_secrets.go|PUT|":                       apiRouteUses("PUT", "/sandboxes/{param}/secrets"),
	"sandbox_policy.go|PATCH|\"/lifecycle-policy\"": apiRouteUses("PATCH", "/sandboxes/{param}/lifecycle-policy"),
	"sandbox_policy.go|POST|\"/reset-idle-timer\"":  apiRouteUses("POST", "/sandboxes/{param}/reset-idle-timer"),
	"preview_endpoints.go|GET|path":                 apiRouteUses("GET", "/sandboxes/{param}/ports"),
	"preview_endpoints.go|PUT|path":                 apiRouteUses("PUT", "/sandboxes/{param}/ports/{param}"),
	"preview_endpoints.go|DELETE|path":              apiRouteUses("DELETE", "/sandboxes/{param}/ports/{param}"),
	"preview_endpoints.go|POST|path[:len(path)-len(\"/ports\")] + \"/signed-preview-urls/revoke\"": apiRouteUses("POST",
		"/sandboxes/{param}/signed-preview-urls/revoke"),
	"preview_links.go|GET|path":                     apiRouteUses("GET", "/sandboxes/{param}/ports/{param}"),
	"preview_links.go|POST|path + \"/preview-url\"": apiRouteUses("POST", "/sandboxes/{param}/ports/{param}/preview-url"),
	"preview_links.go|POST|path + \"/signed-preview-urls\"": apiRouteUses("POST",
		"/sandboxes/{param}/ports/{param}/signed-preview-urls"),
	"process_handle.go|DELETE|":           toolboxRouteUses("DELETE", "/processes/{param}"),
	"process_handle.go|POST|\"/stdin\"":   toolboxRouteUses("POST", "/processes/{param}/stdin"),
	"process_handle.go|POST|\"/resize\"":  toolboxRouteUses("POST", "/processes/{param}/resize"),
	"process_signal.go|POST|\"/signals\"": toolboxRouteUses("POST", "/processes/{param}/signals"),
	"process_logs.go|GET|\"/logs\"":       toolboxRouteUses("GET", "/processes/{param}/logs"),
	"process_wait.go|GET|\"/wait\"":       toolboxRouteUses("GET", "/processes/{param}/wait"),
	"process_wait.go|GET|":                toolboxRouteUses("GET", "/processes/{param}"),
	"computer_use_process.go|GET|\"/computeruse/process/\" + segment + \"/\" + operation": toolboxRouteUses("GET",
		"/computeruse/process/{param}/status", "/computeruse/process/{param}/logs", "/computeruse/process/{param}/errors"),
}

// Guard every Path/Method mutation and the literal-bearing inputs to forwarding
// helpers. These entries deliberately include the actual source expression:
// changing an indirect route must fail just like changing a direct Path literal.
var routeAnchors = map[string][]sdkRoute{
	"build_logs.go|buildLogDiscoveryPath(\"sandboxes\", s.id)":                                                                nil,
	"build_logs.go|buildLogSource{s.client, \"sandboxes\", s.id}":                                                             nil,
	"build_logs.go|buildLogSource{s.client, \"images\", imageID}":                                                             nil,
	"build_logs.go|\"/\" + collection + \"/\" + segment + \"/build-logs-url\"":                                                nil,
	"build_logs_follow.go|buildLogDiscoveryPath(source.collection, source.id)":                                                nil,
	"image_list.go|read(r.path)":                                                                                              nil,
	"image_list.go|read(r.path + \"/search\")":                                                                                nil,
	"image_list.go|prefix + \"/\" + segment":                                                                                  nil,
	"image_wait.go|request.Path, request.Result = r.path, &raw":                                                               nil,
	"images.go|artifactListReader{s.client, \"/images\", &s.client.imageSearchFallback}":                                      nil,
	"images.go|artifactPath(\"/images\", id)":                                                                                 nil,
	"images_ready.go|artifactPath(\"/images\", id)":                                                                           nil,
	"snapshots.go|artifactListReader{s.client, \"/snapshots\", &s.client.snapshotSearchFallback}":                             nil,
	"snapshots.go|artifactPath(\"/snapshots\", id)":                                                                           nil,
	"images_build.go|request.Method, request.Path, request.Result = http.MethodPost, \"/images\", &raw":                       apiRouteUses("POST", "/images"),
	"macos_create.go|prepared.path, prepared.class = \"/sandboxes/macos\", SandboxClassMacOS":                                 nil,
	"sandbox_create.go|prepared.path, prepared.class = \"/sandboxes\", SandboxClassLinux":                                     nil,
	"sandbox_create.go|prepared.path, prepared.class = \"/sandboxes/from-snapshot\", SandboxClassLinux":                       nil,
	"sandbox_lifecycle.go|lifecycleTransition{action: \"stop\", target: SandboxStateStopped}":                                 nil,
	"sandbox_lifecycle.go|lifecycleTransition{action: \"pause\", target: SandboxStatePaused, failure: SandboxStateStarted}":   nil,
	"sandbox_lifecycle.go|lifecycleTransition{action: \"resume\", target: SandboxStateStarted, failure: SandboxStateStopped}": nil,
	"sandbox_lifecycle.go|lifecycleTransition{target: SandboxStateDestroyed}":                                                 nil,
	"sandbox_lifecycle.go|request.Method, request.Path = http.MethodDelete, w.path":                                           apiRouteUses("DELETE", "/sandboxes/{param}"),
	"sandbox_lifecycle_start.go|action := \"\"":                                                                               nil,
	"sandbox_lifecycle_start.go|action = \"resume\"":                                                                          nil,
	"sandbox_lifecycle_start.go|action = \"start\"":                                                                           nil,
	"sandbox_list.go|read(\"/sandboxes\")":                                                                                    nil,
	"sandbox_list.go|read(\"/sandboxes/search\")":                                                                             nil,
	"sandbox_network.go|request.Path = \"/sandboxes/\" + segment + \"/network-policy\"":                                       nil,
	"sandbox_secrets.go|request.Path = \"/sandboxes/\" + segment + \"/secrets\"":                                              nil,
	"sandbox_policy.go|request.Path, request.Result = \"/sandboxes/\"+segment+request.Path, &raw":                             nil,
	"ports.go|\"/sandboxes/\" + segment + \"/ports\"":                                                                         nil,
	"ports.go|base + \"/\" + strconv.Itoa(port)":                                                                              nil,
	"process_handle.go|spec.Path = \"/processes/\" + segment + request.Path":                                                  nil,
	"computer_use_screen.go|spec.Path += \"/region\"":                                                                         toolboxRouteUses("GET", "/computeruse/screenshot/region"),
	"computer_use_screen.go|spec.Path += \"/compressed\"": toolboxRouteUses("GET",
		"/computeruse/screenshot/compressed", "/computeruse/screenshot/region/compressed"),
	"computer_use_process.go|s.read(ctx, name, \"status\")":                         nil,
	"computer_use_process.go|s.read(ctx, name, \"restart\")":                        nil,
	"computer_use_process.go|s.read(ctx, name, \"logs\")":                           nil,
	"computer_use_process.go|s.read(ctx, name, \"errors\")":                         nil,
	"computer_use_process.go|spec.Method = http.MethodPost":                         toolboxRouteUses("POST", "/computeruse/process/{param}/restart"),
	"computer_use_process.go|operation == \"restart\"":                              nil,
	"sandbox_wait.go|path: \"/sandboxes/\" + segment":                               nil,
	"pty_connection.go|relativeURL(base, \"/processes/\"+segment+\"/attach\", nil)": toolboxRouteUses("GET", "/processes/{param}/attach"),
}

func apiRouteUses(method string, paths ...string) []sdkRoute {
	routes := make([]sdkRoute, 0, len(paths))
	for _, path := range paths {
		routes = append(routes, sdkRoute{"api", method, path})
	}
	return routes
}

func toolboxRouteUses(method string, paths ...string) []sdkRoute {
	routes := apiRouteUses(method, paths...)
	for index := range routes {
		routes[index].contract = "toolbox"
	}
	return routes
}
