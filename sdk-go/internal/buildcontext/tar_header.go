// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"strconv"
	"strings"
)

const tarBlock = 512

type tarHeader struct {
	name string
	mode int64
	size int64
	kind byte
	link string
}

// CPython's PAX stream layout differs from archive/tar's PAX writer (extension
// names, field truncation, and final record padding). Keep these wire bytes
// explicit because the complete tar digest is the cross-SDK upload identity.
func (h tarHeader) bytes() []byte {
	block := make([]byte, tarBlock)
	copy(block[:100], asciiField(h.name))
	putOctal(block[100:108], h.mode&0o7777)
	putOctal(block[108:116], 0)
	putOctal(block[116:124], 0)
	putOctal(block[124:136], h.size)
	putOctal(block[136:148], 0)
	copy(block[148:156], "        ")
	block[156] = h.kind
	copy(block[157:257], asciiField(h.link))
	copy(block[257:263], "ustar\x00")
	copy(block[263:265], "00")
	var checksum int64
	for _, b := range block {
		checksum += int64(b)
	}
	putOctal(block[148:155], checksum)
	block[155] = ' '
	return block
}

func putOctal(field []byte, value int64) {
	octal := strconv.FormatInt(value, 8)
	for i := range len(field) - 1 {
		field[i] = '0'
	}
	copy(field[len(field)-1-len(octal):], octal)
	field[len(field)-1] = 0
}

func asciiField(value string) string {
	var output strings.Builder
	for _, r := range value {
		if output.Len() == 100 {
			break
		}
		if r > 127 {
			output.WriteByte('?')
		} else {
			output.WriteRune(r)
		}
	}
	return output.String()
}

func needsPAX(value string) bool {
	return len(value) > 100 || strings.ContainsFunc(value, func(r rune) bool { return r > 127 })
}

func paxRecord(key, value string) string {
	record := " " + key + "=" + value + "\n"
	length := len(record) + 1
	for len(strconv.Itoa(length))+len(record) != length {
		length = len(strconv.Itoa(length)) + len(record)
	}
	return strconv.Itoa(length) + record
}

func padding(size int64) int64 { return (tarBlock - size%tarBlock) % tarBlock }
