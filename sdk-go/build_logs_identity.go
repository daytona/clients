// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/hex"
	"errors"
	"math/big"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

var (
	buildLogUUIDPattern  = regexp.MustCompile(`(?i)^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$`)
	buildLogRoutePattern = regexp.MustCompile(`/build-logs/([^/]+)/([^/%]+)$`)
)

// errBuildLogIdentity marks a destination naming another resource than the
// one discovery asked about.
var errBuildLogIdentity = errors.New("unexpected build log identity")

// buildLogDestination validates raw. Its resource ID must be the one
// discoveryPath names, or canonicalID when the caller resolved that value (an
// image name) to its ID.
func buildLogDestination(api *apiClient, raw, discoveryPath, canonicalID string) (*buildLogTarget, error) {
	target, err := parseBuildLogURL(raw)
	if err != nil {
		return nil, err
	}
	requested := strings.Split(strings.TrimPrefix(discoveryPath, "/"), "/")
	actual := buildLogRoutePattern.FindStringSubmatch(target.EscapedPath())
	if len(requested) != 3 || actual == nil {
		return nil, buildLogProtocol("unexpected build log route")
	}
	collection := strings.ReplaceAll(strings.ReplaceAll(actual[1], "%2e", "."), "%2E", ".")
	if collection == "." || collection == ".." {
		return nil, buildLogProtocol("unexpected build log collection")
	}
	id, err := url.PathUnescape(requested[1])
	if err != nil {
		return nil, buildLogProtocol("unexpected build log identity")
	}
	if canonicalID != "" {
		id = canonicalID
	}
	if !sameBuildLogResource(requested[0], id, actual[2]) {
		failure := buildLogProtocol("unexpected build log identity")
		failure.Err = errBuildLogIdentity
		return nil, failure
	}
	scoped := *api
	scoped.baseURL = target.Scheme + "://" + target.Host
	return &buildLogTarget{api: &scoped, path: target.EscapedPath()}, nil
}

func parseBuildLogURL(raw string) (*url.URL, error) {
	if raw == "" || utf8.RuneCountInString(raw) > 4096 ||
		strings.ContainsAny(raw, "\\?#") ||
		strings.ContainsFunc(raw, func(r rune) bool { return r <= 32 || r == 127 }) {
		return nil, buildLogProtocol("invalid build log URL")
	}
	target, err := url.Parse(raw)
	if err != nil || target.Hostname() == "" || target.User != nil ||
		(target.Scheme != "http" && target.Scheme != "https") {
		return nil, buildLogProtocol("invalid build log URL")
	}
	if port := target.Port(); port != "" {
		if _, portErr := strconv.ParseUint(port, 10, 16); portErr != nil {
			return nil, buildLogProtocol("invalid build log URL port")
		}
	}
	return target, nil
}

func sameBuildLogResource(collection, requested, actual string) bool {
	if requested == actual {
		return true
	}
	left, leftRegion, leftOK := buildLogIdentity(requested, collection == "sandboxes")
	right, rightRegion, rightOK := buildLogIdentity(actual, collection == "sandboxes")
	return leftOK && rightOK && left == right &&
		(leftRegion == "" || rightRegion == "" || leftRegion == rightRegion)
}

func buildLogIdentity(value string, sandbox bool) (string, string, bool) {
	uuid := value
	switch {
	case strings.HasPrefix(value, "urn:uuid:"):
		uuid = value[9:]
	case strings.HasPrefix(value, "{") && strings.HasSuffix(value, "}"):
		uuid = value[1 : len(value)-1]
	}
	if buildLogUUIDPattern.MatchString(uuid) {
		return strings.ToLower(strings.ReplaceAll(uuid, "-", "")), "", true
	}
	if !sandbox {
		return "", "", false
	}
	body, region, tagged := strings.Cut(value, "-")
	if len(body) == 0 || len(body) > 22 || (tagged && !buildLogRegion(region)) {
		return "", "", false
	}
	number := new(big.Int)
	base := big.NewInt(58)
	for _, char := range body {
		digit := strings.IndexRune("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz", char)
		if digit < 0 {
			return "", "", false
		}
		number.Mul(number, base)
		number.Add(number, big.NewInt(int64(digit)))
	}
	zeros := len(body) - len(strings.TrimLeft(body, "1"))
	if zeros+(number.BitLen()+7)/8 != 16 {
		return "", "", false
	}
	return hex.EncodeToString(number.FillBytes(make([]byte, 16))), region, true
}

func buildLogRegion(region string) bool {
	if len(region) == 0 || len(region) > 64 {
		return false
	}
	for _, char := range region {
		switch {
		case char >= 'A' && char <= 'Z':
		case char >= 'a' && char <= 'z':
		case char >= '0' && char <= '9':
		case char == '_', char == '-':
		default:
			return false
		}
	}
	return true
}
