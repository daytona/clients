// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"time"
)

// desktopDecoder accumulates the first malformed-field error at the boundary.
// Keeping raw fields here also permits the contract's named extra-field bags.
type desktopDecoder struct {
	object jsonObject
	err    error
}

func decodeDesktop(raw JSONValue) *desktopDecoder {
	object, err := parseObject(raw, "computer-use")
	return &desktopDecoder{object: object, err: err}
}

func desktopField[T any](d *desktopDecoder, name string, required bool) T {
	value, err := field[T](d.object, name, required)
	d.check(err)
	return value
}

func (d *desktopDecoder) check(err error) {
	if d.err == nil {
		d.err = err
	}
}

func (d *desktopDecoder) text(name string) string  { return desktopField[string](d, name, true) }
func (d *desktopDecoder) boolean(name string) bool { return desktopField[bool](d, name, true) }

func (d *desktopDecoder) integer(name string, bounds [2]int64) int64 {
	value, err := integerField(d.object, name, true)
	d.check(err)
	if value < bounds[0] || value > bounds[1] {
		d.check(badRequest("response field " + name + " is outside its integer range"))
	}
	return value
}

func (d *desktopDecoder) int32(name string) int {
	value := d.integer(name, [2]int64{math.MinInt32, math.MaxInt32})
	if value < math.MinInt32 || value > math.MaxInt32 {
		return 0 // d.integer recorded the range error
	}
	return int(value)
}

func (d *desktopDecoder) nonnegative32(name string) int {
	value := d.integer(name, [2]int64{0, math.MaxInt32})
	if value < 0 || value > math.MaxInt32 {
		return 0 // d.integer recorded the range error
	}
	return int(value)
}

func (d *desktopDecoder) safeInteger(name string) int64 {
	return d.integer(name, [2]int64{0, 1<<53 - 1})
}

func (d *desktopDecoder) nonnegativeInteger(name string) int64 {
	return d.integer(name, [2]int64{0, math.MaxInt64})
}

func (d *desktopDecoder) timestamp(name string, required bool) time.Time {
	if required {
		d.text(name)
	}
	value, err := timestampField(d.object, name)
	d.check(err)
	return value
}

func (d *desktopDecoder) extras(known ...string) map[string]JSONValue {
	extras := make(map[string]JSONValue)
	for key, value := range d.object {
		extras[key] = value
	}
	for _, key := range known {
		delete(extras, key)
	}
	if len(extras) == 0 {
		return nil
	}
	return extras
}

func parseDesktopPoint(raw JSONValue) (*ComputerUsePoint, error) {
	d := decodeDesktop(raw)
	result := &ComputerUsePoint{X: d.int32("x"), Y: d.int32("y")}
	return result, d.err
}
