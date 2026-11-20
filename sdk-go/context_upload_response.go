// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/url"
	"strconv"
	"strings"
	"unicode"
)

type contextUploadDeclaration struct {
	upload buildContextUpload
	ready  bool
	parts  []contextUploadPart
}

// Presigned URLs stay out of diagnostics, including formatted internal values.
type contextUploadPart struct {
	number int64
	bytes  int64
	offset int64
	url    string
}

func (p contextUploadPart) String() string   { return "build context part (URL redacted)" }
func (p contextUploadPart) GoString() string { return p.String() }

func parseContextUpload(raw json.RawMessage) (contextUploadDeclaration, error) {
	var declaration contextUploadDeclaration
	object, err := parseObject(raw, "build context upload")
	if err != nil {
		return declaration, err
	}
	id, err := contextUploadText(object, "id")
	if err != nil {
		return declaration, err
	}
	if err = checkResponseID(id); err != nil {
		return declaration, err
	}
	region, err := contextUploadText(object, "region_id")
	if err != nil {
		return declaration, err
	}
	state, err := contextUploadText(object, "state")
	if err != nil {
		return declaration, err
	}
	rawParts, err := field[[]json.RawMessage](object, "parts", true)
	if err != nil {
		return declaration, err
	}
	parts := make([]contextUploadPart, 0, len(rawParts))
	for _, rawPart := range rawParts {
		part, parseErr := parseContextPart(rawPart)
		if parseErr != nil {
			return declaration, parseErr
		}
		parts = append(parts, part)
	}
	return contextUploadDeclaration{
		upload: buildContextUpload{ID: id, Region: region}, ready: state == "ready", parts: parts,
	}, nil
}

func contextUploadText(object jsonObject, key string) (string, error) {
	text, err := field[string](object, key, true)
	if err != nil {
		return "", err
	}
	if text == "" {
		return "", badRequest("build context upload response has no " + key)
	}
	return text, nil
}

func parseContextPart(raw json.RawMessage) (contextUploadPart, error) {
	var part contextUploadPart
	object, err := parseObject(raw, "build context upload part")
	if err != nil {
		return part, err
	}
	number, err := integerField(object, "part_number", true)
	if err != nil {
		return part, err
	}
	size, err := integerField(object, "bytes", true)
	if err != nil {
		return part, err
	}
	if number < -maxSafeInteger || number > maxSafeInteger || size < -maxSafeInteger || size > maxSafeInteger {
		return part, badRequest("build context upload response has an unsafe part integer")
	}
	endpoint, err := field[string](object, "url", true)
	if err != nil {
		return part, err
	}
	return contextUploadPart{number: number, bytes: size, url: endpoint}, nil
}

func contextUploadLayout(parts []contextUploadPart, size int64) ([]contextUploadPart, error) {
	checked := make([]contextUploadPart, len(parts))
	var offset int64
	for index, part := range parts {
		if part.number != int64(index)+1 || part.bytes <= 0 || part.bytes > size-offset {
			return nil, badRequest("build context upload parts do not cover the archive")
		}
		if err := contextStorageURL(part.url); err != nil {
			return nil, err
		}
		part.offset = offset
		checked[index] = part
		offset += part.bytes
	}
	if offset != size {
		return nil, badRequest("build context upload parts do not cover the archive")
	}
	return checked, nil
}

func contextStorageURL(raw string) error {
	endpoint, err := url.Parse(raw)
	if err != nil || (endpoint.Scheme != "http" && endpoint.Scheme != "https") ||
		endpoint.Hostname() == "" || endpoint.User != nil || endpoint.Opaque != "" ||
		strings.ContainsAny(raw, "\\#") || strings.ContainsFunc(raw, func(char rune) bool {
		return unicode.IsSpace(char) || unicode.IsControl(char)
	}) {
		// Neither the URL nor a URL parse error may reach the error chain.
		return badRequest("build context upload response has an invalid storage URL")
	}
	if port := endpoint.Port(); port != "" {
		if _, portErr := strconv.ParseUint(port, 10, 16); portErr != nil {
			return badRequest("build context upload response has an invalid storage URL")
		}
	}
	return nil
}
