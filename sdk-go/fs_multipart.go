// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/url"
	"strings"
	"unicode/utf8"
)

func parseFileMultipart(body []byte, contentType string) (*BulkReadResult, error) {
	boundary := "DAYTONA-FILE-BOUNDARY"
	if contentType != "" {
		_, params, err := mime.ParseMediaType(contentType)
		if err != nil {
			return nil, badRequest("invalid multipart content type")
		}
		if value := params["boundary"]; value != "" {
			boundary = value
		}
	}
	if !bytes.HasPrefix(body, []byte("--"+boundary)) {
		return nil, badRequest("invalid multipart response boundary")
	}
	result := &BulkReadResult{Files: make(map[string][]byte), Errors: []BulkReadError{}}
	reader := multipart.NewReader(bytes.NewReader(body), boundary)
	for {
		part, err := reader.NextRawPart()
		if errors.Is(err, io.EOF) {
			return result, nil
		}
		if err != nil {
			return nil, wrapJSONError(err)
		}
		data, readErr := io.ReadAll(part)
		closeErr := part.Close()
		if bodyErr := errors.Join(readErr, closeErr); bodyErr != nil {
			return nil, wrapJSONError(bodyErr)
		}
		if partErr := addFilePart(result, part.Header.Get("Content-Disposition"), data); partErr != nil {
			return nil, partErr
		}
	}
}

func addFilePart(result *BulkReadResult, disposition string, data []byte) error {
	_, params, err := mime.ParseMediaType(disposition)
	if err != nil {
		return badRequest("invalid multipart content disposition")
	}
	switch params["name"] {
	case "file":
		filename, nameErr := filePartName(disposition, params)
		if nameErr != nil {
			return nameErr
		}
		result.Files[filename] = data
	case "error":
		entry, parseErr := parseFileReadError(data)
		if parseErr != nil {
			return parseErr
		}
		result.Errors = append(result.Errors, entry)
	default:
		// Future multipart part types are ignored.
	}
	return nil
}

func filePartName(disposition string, params map[string]string) (string, error) {
	// mime.ParseMediaType silently ignores invalid RFC 5987 encodings. The SDK
	// contract rejects them instead; decode this attribute before using fallback.
	for _, parameter := range strings.Split(disposition, ";") {
		name, value, found := strings.Cut(strings.TrimSpace(parameter), "=")
		if found && strings.EqualFold(name, "filename*") {
			value = strings.Trim(value, "\"")
			encoded, valid := strings.CutPrefix(strings.ToLower(value), "utf-8''")
			if !valid {
				return "", badRequest("multipart filename* must use UTF-8")
			}
			// Preserve the original case of the encoded filename.
			decoded, err := url.PathUnescape(value[len(value)-len(encoded):])
			if err != nil || !utf8.ValidString(decoded) {
				return "", badRequest("multipart filename* is not percent-encoded UTF-8")
			}
			return decoded, nil
		}
	}
	filename, exists := params["filename"]
	if !exists {
		return "", badRequest("multipart file part missing filename")
	}
	// Do not call multipart.Part.FileName: it discards directory components.
	return filename, nil
}

func parseFileReadError(data []byte) (BulkReadError, error) {
	if err := checkJSON(data); err != nil {
		return BulkReadError{}, err
	}
	object, err := parseObject(data, "bulk download error")
	if err != nil {
		return BulkReadError{}, err
	}
	path, pathErr := field[string](object, "path", true)
	message, messageErr := field[string](object, "message", true)
	code, codeErr := field[string](object, "code", false)
	source, sourceErr := field[string](object, "source", false)
	return BulkReadError{Path: path, Message: message, Code: code, Source: source},
		errors.Join(pathErr, messageErr, codeErr, sourceErr)
}
