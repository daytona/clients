// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/hex"
	"fmt"
	"math/big"
	"net/url"
	"regexp"
	"strings"
	"unicode"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/google/uuid"
)

var buildLogRegionPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// validatedBuildLogBaseURL accepts only absolute HTTP(S) URLs without credentials,
// query parameters or fragments, and verifies that the path identifies the
// requested resource with no trailing segments.
func validatedBuildLogBaseURL(discoveryPath, raw string) (string, error) {
	if strings.TrimSpace(raw) == "" {
		return "", cerr.New(cerr.ExitFailure, "build log discovery returned no URL")
	}
	if strings.IndexFunc(raw, unicode.IsSpace) >= 0 {
		return "", invalidBuildLogURLError(raw)
	}
	target, err := url.Parse(raw)
	if err != nil || !target.IsAbs() || target.Host == "" {
		return "", invalidBuildLogURLError(raw)
	}
	if target.Scheme != "http" && target.Scheme != "https" {
		return "", invalidBuildLogURLError(raw)
	}
	if target.User != nil || target.RawQuery != "" || target.Fragment != "" {
		return "", invalidBuildLogURLError(raw)
	}
	requestedCollection, requestedID, ok := buildLogDiscoveryTarget(discoveryPath)
	if !ok || !strings.Contains(target.Path, "/build-logs/") {
		return "", invalidBuildLogURLError(raw)
	}
	actualCollection, actualID, ok := buildLogRouteTarget(target.Path)
	if !ok || requestedCollection != actualCollection || !sameBuildLogResource(requestedID, actualID) {
		return "", invalidBuildLogURLError(raw)
	}
	target.RawQuery = ""
	target.Fragment = ""
	return target.String(), nil
}

func invalidBuildLogURLError(raw string) error {
	return cerr.New(cerr.ExitFailure, "invalid build log URL %q", sanitizeBuildLogURL(raw))
}

func sanitizeBuildLogURL(raw string) string {
	if parsed, err := url.Parse(raw); err == nil && parsed.Scheme != "" && parsed.Host != "" {
		parsed.User = nil
		parsed.RawQuery = ""
		parsed.Fragment = ""
		return strings.TrimSpace(parsed.String())
	}
	var builder strings.Builder
	builder.Grow(len(raw))
	for _, r := range raw {
		switch {
		case unicode.IsSpace(r), unicode.IsControl(r):
			builder.WriteRune(' ')
		default:
			builder.WriteRune(r)
		}
		if builder.Len() >= 200 {
			builder.WriteString("…")
			return strings.TrimSpace(builder.String())
		}
	}
	return strings.TrimSpace(builder.String())
}

func buildLogDiscoveryTarget(path string) (collection, id string, ok bool) {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) != 3 || parts[2] != "build-logs-url" {
		return "", "", false
	}
	collection, ok = buildLogPathSegment(parts[0])
	if !ok {
		return "", "", false
	}
	id, ok = buildLogPathSegment(parts[1])
	if !ok {
		return "", "", false
	}
	return collection, id, true
}

func buildLogRouteTarget(path string) (collection, id string, ok bool) {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	for i := 0; i+2 < len(parts); i++ {
		if parts[i] != "build-logs" {
			continue
		}
		collection, ok = buildLogPathSegment(parts[i+1])
		if !ok {
			return "", "", false
		}
		id, ok = buildLogPathSegment(parts[i+2])
		if !ok {
			return "", "", false
		}
		if i+3 != len(parts) {
			return "", "", false
		}
		return collection, id, true
	}
	return "", "", false
}

func buildLogPathSegment(raw string) (string, bool) {
	decoded, err := url.PathUnescape(raw)
	if err != nil || decoded == "" || decoded == "." || decoded == ".." || strings.ContainsAny(decoded, "/\\") || strings.IndexFunc(decoded, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) >= 0 {
		return "", false
	}
	return decoded, true
}

func sameBuildLogResource(requested, actual string) bool {
	if requested == actual {
		return true
	}
	left, ok := buildLogIdentity(requested)
	if !ok {
		return false
	}
	right, ok := buildLogIdentity(actual)
	if !ok {
		return false
	}
	return left.uuid == right.uuid && (left.region == "" || right.region == "" || left.region == right.region)
}

type buildLogResourceIdentity struct {
	uuid   string
	region string
}

func buildLogIdentity(value string) (buildLogResourceIdentity, bool) {
	normalized := strings.TrimPrefix(value, "urn:uuid:")
	if strings.HasPrefix(normalized, "{") && strings.HasSuffix(normalized, "}") {
		normalized = normalized[1 : len(normalized)-1]
	}
	if parsed, err := uuid.Parse(normalized); err == nil {
		return buildLogResourceIdentity{uuid: strings.ReplaceAll(parsed.String(), "-", "")}, true
	}
	body, region := value, ""
	if dash := strings.IndexByte(value, '-'); dash >= 0 {
		body = value[:dash]
		region = value[dash+1:]
		if region == "" {
			return buildLogResourceIdentity{}, false
		}
	}
	if body == "" || len(body) > 22 || (region != "" && !buildLogRegionPattern.MatchString(region)) {
		return buildLogResourceIdentity{}, false
	}
	decoded, err := decodeBuildLogBase58(body)
	if err != nil || len(decoded) != 16 {
		return buildLogResourceIdentity{}, false
	}
	return buildLogResourceIdentity{uuid: hex.EncodeToString(decoded), region: region}, true
}

func decodeBuildLogBase58(value string) ([]byte, error) {
	const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
	number := big.NewInt(0)
	base := big.NewInt(58)
	for _, r := range value {
		index := strings.IndexRune(alphabet, r)
		if index < 0 {
			return nil, fmt.Errorf("invalid base58 digit")
		}
		number.Mul(number, base)
		number.Add(number, big.NewInt(int64(index)))
	}
	decoded := number.Bytes()
	leadingZeros := 0
	for leadingZeros < len(value) && value[leadingZeros] == '1' {
		leadingZeros++
	}
	if leadingZeros == 0 {
		return decoded, nil
	}
	prefixed := make([]byte, leadingZeros+len(decoded))
	copy(prefixed[leadingZeros:], decoded)
	return prefixed, nil
}
