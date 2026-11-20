// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"errors"
	"strconv"
	"time"
)

// accountObject tracks the first malformed field across a nested response.
// Raw JSON remains confined to this parsing boundary.
type accountObject struct {
	fields jsonObject
	path   string
	err    *error
}

func accountResponse(data json.RawMessage, description string) accountObject {
	fields, err := parseObject(data, description)
	return accountObject{fields: fields, path: description, err: &err}
}

func (o accountObject) fail(name string, err error) {
	if err != nil && *o.err == nil {
		message := err.Error()
		var sdkError *Error
		if errors.As(err, &sdkError) {
			message = sdkError.Message
		}
		*o.err = badRequest(o.path + "." + name + ": " + message)
	}
}

func (o accountObject) text(name string, required bool) string {
	value, err := field[string](o.fields, name, required)
	o.fail(name, err)
	return value
}

func (o accountObject) timestamp(name string, required bool) time.Time {
	if required && !present(o.fields[name]) {
		o.fail(name, badRequest("required timestamp is missing"))
	}
	value, err := timestampField(o.fields, name)
	o.fail(name, err)
	return value
}

func (o accountObject) nullable(name string) {
	if len(o.fields[name]) == 0 {
		o.fail(name, badRequest("required nullable field is missing"))
	}
}

func (o accountObject) integer(name string) int64 {
	value, err := integerField(o.fields, name, true)
	o.fail(name, err)
	return value
}

func (o accountObject) optionalInteger(name string, nullable bool) *int64 {
	if nullable {
		o.nullable(name)
	}
	if !present(o.fields[name]) {
		return nil
	}
	value := o.integer(name)
	return &value
}

func (o accountObject) number(name string) float64 {
	value, err := field[float64](o.fields, name, true)
	o.fail(name, err)
	return value
}

func (o accountObject) nested(name string, optional bool) *accountObject {
	if optional && !present(o.fields[name]) {
		return nil
	}
	fields, err := parseObject(o.fields[name], o.path+"."+name)
	o.fail(name, err)
	return &accountObject{fields: fields, path: o.path + "." + name, err: o.err}
}

func (o accountObject) array(name string) []json.RawMessage {
	values, err := field[[]json.RawMessage](o.fields, name, true)
	o.fail(name, err)
	return values
}

func (o accountObject) strings(name string) []string {
	items := o.array(name)
	values := make([]string, len(items))
	for i, raw := range items {
		itemName := name + "[" + strconv.Itoa(i) + "]"
		value, err := field[string](jsonObject{itemName: raw}, itemName, true)
		o.fail(itemName, err)
		values[i] = value
	}
	return values
}

func (o accountObject) objectItem(name string, raw json.RawMessage) accountObject {
	fields, err := parseObject(raw, o.path+"."+name)
	o.fail(name, err)
	return accountObject{fields: fields, path: o.path + "." + name, err: o.err}
}
