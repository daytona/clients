// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"cmp"
	"context"
	"errors"
	"io"
	"os"
	"slices"
)

type tarWriter struct {
	ctx    context.Context
	output io.Writer
	total  int64
}

func (w *tarWriter) write(data []byte) error {
	if err := w.ctx.Err(); err != nil {
		return err
	}
	if int64(len(data)) > MaxBytes-w.total {
		return invalid("build context exceeds the 1 GiB limit", nil)
	}
	n, err := w.output.Write(data)
	w.total += int64(n)
	if err != nil {
		return err
	}
	if n != len(data) {
		return io.ErrShortWrite
	}
	return nil
}

func (w *tarWriter) writeEntries(entries []entry) error {
	ordered := slices.Clone(entries)
	slices.SortFunc(ordered, func(a, b entry) int { return cmp.Compare(a.name, b.name) })
	for _, item := range ordered {
		if err := w.writeEntry(item); err != nil {
			return err
		}
	}
	if err := w.write(make([]byte, 1024)); err != nil {
		return err
	}
	return w.write(make([]byte, (10240-w.total%10240)%10240))
}

func (w *tarWriter) writeEntry(item entry) (err error) {
	header := tarHeader{name: item.name, mode: item.mode}
	var input *os.File
	switch item.kind {
	case fileEntry:
		input, header.size, err = item.open()
		if err != nil {
			return err
		}
		defer func() { err = errors.Join(err, input.Close()) }()
		header.kind = '0'
	case bytesEntry:
		header.kind, header.size = '0', int64(len(item.content))
	case dirEntry:
		header.kind = '5'
	case symlinkEntry:
		header.kind, header.link = '2', item.target
	}
	if header.size < 0 || header.size > MaxBytes ||
		w.total+tarBlock+header.size+padding(header.size)+1024 > MaxBytes {
		return invalid("build context exceeds the 1 GiB limit", nil)
	}
	if err = w.writeHeader(header); err != nil {
		return err
	}
	switch item.kind {
	case fileEntry:
		err = w.copyFile(input, header.size)
		if errors.Is(err, io.ErrUnexpectedEOF) {
			err = invalid("build context changed: "+quote(item.local), err)
		}
	case bytesEntry:
		err = w.write(item.content)
	case dirEntry, symlinkEntry:
	}
	if err != nil {
		return err
	}
	return w.write(make([]byte, padding(header.size)))
}

func (w *tarWriter) writeHeader(header tarHeader) error {
	var extended string
	if needsPAX(header.name) {
		extended += paxRecord("path", header.name)
	}
	if needsPAX(header.link) {
		extended += paxRecord("linkpath", header.link)
	}
	if extended != "" {
		pax := tarHeader{name: "././@PaxHeader", size: int64(len(extended)), kind: 'x'}
		if err := w.write(pax.bytes()); err != nil {
			return err
		}
		if err := w.write([]byte(extended)); err != nil {
			return err
		}
		if err := w.write(make([]byte, padding(pax.size))); err != nil {
			return err
		}
	}
	return w.write(header.bytes())
}

func (w *tarWriter) copyFile(input io.Reader, size int64) error {
	chunk := make([]byte, 64*1024)
	for remaining := size; remaining > 0; {
		if err := w.ctx.Err(); err != nil {
			return err
		}
		n, err := io.ReadFull(input, chunk[:min(int64(len(chunk)), remaining)])
		if errors.Is(err, io.EOF) {
			return io.ErrUnexpectedEOF
		}
		if err != nil {
			return err
		}
		if err = w.write(chunk[:n]); err != nil {
			return err
		}
		remaining -= int64(n)
	}
	return nil
}
