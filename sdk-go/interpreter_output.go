// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"encoding/json"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

func parseInterpreterRecord(payload string) (jsonObject, bool, error) {
	data := []byte(payload)
	if !utf8.ValidString(payload) || !json.Valid(data) {
		return nil, false, nil
	}
	if err := checkJSON(data); err != nil {
		return nil, false, err
	}
	if bytes.TrimSpace(data)[0] != '{' {
		return nil, false, nil
	}
	object, err := parseObject(data, "interpreter frame")
	return object, true, err
}

func parseInterpreterOutput(frame LogFrame) (InterpreterOutput, bool, error) {
	object, valid, err := parseInterpreterRecord(frame.Data)
	if err != nil || !valid {
		return InterpreterOutput{}, false, err
	}
	data := object
	raw, wrapped := object["data"]
	switch {
	case !wrapped:
		// A direct object is already a MIME bundle.
	case bytes.TrimSpace(raw)[0] == '{':
		nested, parseErr := parseObject(raw, "MIME bundle")
		if parseErr != nil {
			return InterpreterOutput{}, false, parseErr
		}
		data = nested
	default:
		mime := interpreterStringMIME(object["mime"])
		if mime == nil {
			return InterpreterOutput{}, false, nil
		}
		data = jsonObject{*mime: raw}
	}
	return InterpreterOutput{
		Data: data, IsMainResult: frame.Channel == LogChannelExecuteResult,
		Text: interpreterStringMIME(data["text/plain"]), HTML: interpreterStringMIME(data["text/html"]),
		PNG: interpreterStringMIME(data["image/png"]), SVG: interpreterStringMIME(data["image/svg+xml"]),
		JSON: data["application/json"],
	}, true, nil
}

func interpreterStringMIME(raw JSONValue) *string {
	if !present(raw) {
		return nil
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil
	}
	return &value
}

func parseInterpreterError(payload string) (InterpreterError, bool, error) {
	object, valid, err := parseInterpreterRecord(payload)
	if err != nil || !valid {
		return InterpreterError{}, false, err
	}
	result := InterpreterError{
		Name: interpreterCoercedString(object["ename"]), Value: interpreterCoercedString(object["evalue"]),
		Traceback: []string{},
	}
	var lines []json.RawMessage
	if err = json.Unmarshal(object["traceback"], &lines); err == nil {
		for _, line := range lines {
			if bytes.Equal(bytes.TrimSpace(line), []byte("null")) {
				result.Traceback = append(result.Traceback, "null")
			} else {
				result.Traceback = append(result.Traceback, interpreterCoercedString(line))
			}
		}
	}
	return result, true, nil
}

// Match JavaScript String(value) for the deliberately permissive kernel error
// payload, including arrays and objects; nullish name/value fields become empty.
func interpreterCoercedString(raw JSONValue) string {
	if !present(raw) {
		return ""
	}
	raw = bytes.TrimSpace(raw)
	switch raw[0] {
	case '"':
		value := interpreterStringMIME(raw)
		if value != nil {
			return *value
		}
	case '{':
		return "[object Object]"
	case '[':
		var values []json.RawMessage
		if err := json.Unmarshal(raw, &values); err == nil {
			parts := make([]string, len(values))
			for index, value := range values {
				parts[index] = interpreterCoercedString(value)
			}
			return strings.Join(parts, ",")
		}
	default:
		number, err := strconv.ParseFloat(string(raw), 64)
		switch {
		case math.IsInf(number, 1):
			return "Infinity"
		case math.IsInf(number, -1):
			return "-Infinity"
		case err != nil:
			return string(raw)
		case number == 0:
			return "0"
		case math.Abs(number) >= 1e21 || math.Abs(number) < 1e-6:
			formatted := strconv.FormatFloat(number, 'e', -1, 64)
			return strings.ReplaceAll(strings.ReplaceAll(formatted, "e+0", "e+"), "e-0", "e-")
		default:
			return strconv.FormatFloat(number, 'f', -1, 64)
		}
	}
	return ""
}
