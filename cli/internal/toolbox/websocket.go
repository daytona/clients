// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bufio"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
)

// maxWebSocketFrame bounds a single inbound frame: PTY output arrives in
// small frames, and a 64-bit length from a hostile peer must not become an
// allocation.
const maxWebSocketFrame = 16 << 20

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

type WebSocketConn struct {
	conn net.Conn
	r    *bufio.Reader
	wmu  sync.Mutex
}

// ReadMessage returns the next complete data message, reassembling
// fragments (RFC 6455 section 5.4). Fragment state is an explicit flag, not the
// buffer length: a fragment may legally be EMPTY, so `len(message)` cannot
// tell "no message in progress" from "first fragment carried no bytes".
// Control frames (ping/pong/close) may interleave with the fragments.
func (c *WebSocketConn) ReadMessage() ([]byte, error) {
	var (
		inFragment bool
		message    []byte
	)
	for {
		fin, opcode, payload, err := c.readFrame()
		if err != nil {
			// EOF is reserved for an actual WebSocket close frame. A peer
			// dropping TCP has not confirmed the process stream completed.
			if errors.Is(err, io.EOF) {
				return nil, io.ErrUnexpectedEOF
			}
			return nil, err
		}
		switch opcode {
		case 0x1, 0x2:
			if inFragment {
				return nil, fmt.Errorf("received websocket opcode %d before finishing a fragmented message", opcode)
			}
			if fin {
				return payload, nil
			}
			inFragment = true
			message = append(message[:0], payload...)
		case 0x8:
			_ = c.Close()
			return nil, io.EOF
		case 0x9:
			if err := c.writeFrame(0xA, payload); err != nil {
				return nil, err
			}
		case 0xA:
			continue
		case 0x0:
			if !inFragment {
				return nil, fmt.Errorf("received websocket continuation frame without a fragmented message")
			}
			if len(message)+len(payload) > maxWebSocketFrame {
				return nil, fmt.Errorf("websocket message of %d bytes exceeds the %d byte limit", len(message)+len(payload), maxWebSocketFrame)
			}
			message = append(message, payload...)
			if fin {
				return message, nil
			}
		default:
			return nil, fmt.Errorf("unsupported websocket opcode %d", opcode)
		}
	}
}

func (c *WebSocketConn) WriteBinary(payload []byte) error {
	return c.writeFrame(0x2, payload)
}

func (c *WebSocketConn) Close() error {
	_ = c.writeFrame(0x8, nil)
	return c.conn.Close()
}

func (c *WebSocketConn) readFrame() (bool, byte, []byte, error) {
	header := make([]byte, 2)
	if _, err := io.ReadFull(c.r, header); err != nil {
		return false, 0, nil, err
	}
	fin := header[0]&0x80 != 0
	opcode := header[0] & 0x0F
	masked := header[1]&0x80 != 0
	length, err := websocketPayloadLength(c.r, header[1]&0x7F)
	if err != nil {
		return false, 0, nil, err
	}
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(c.r, mask[:]); err != nil {
			return false, 0, nil, err
		}
	}
	if length > maxWebSocketFrame {
		return false, 0, nil, fmt.Errorf("websocket frame of %d bytes exceeds the %d byte limit", length, maxWebSocketFrame)
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(c.r, payload); err != nil {
		return false, 0, nil, err
	}
	if masked {
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
	}
	return fin, opcode, payload, nil
}

func (c *WebSocketConn) writeFrame(opcode byte, payload []byte) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	var header []byte
	first := byte(0x80 | opcode)
	length := len(payload)
	switch {
	case length <= 125:
		header = []byte{first, byte(length) | 0x80}
	case length <= 65535:
		header = make([]byte, 4)
		header[0] = first
		header[1] = 126 | 0x80
		binary.BigEndian.PutUint16(header[2:], uint16(length))
	default:
		header = make([]byte, 10)
		header[0] = first
		header[1] = 127 | 0x80
		binary.BigEndian.PutUint64(header[2:], uint64(length))
	}
	var mask [4]byte
	if _, err := rand.Read(mask[:]); err != nil {
		return fmt.Errorf("generate websocket mask: %w", err)
	}
	masked := make([]byte, len(payload))
	copy(masked, payload)
	for i := range masked {
		masked[i] ^= mask[i%4]
	}
	if _, err := c.conn.Write(header); err != nil {
		return err
	}
	if _, err := c.conn.Write(mask[:]); err != nil {
		return err
	}
	if _, err := c.conn.Write(masked); err != nil {
		return err
	}
	return nil
}

func websocketPayloadLength(r io.Reader, lengthByte byte) (int, error) {
	switch lengthByte {
	case 126:
		var ext [2]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return 0, err
		}
		return int(binary.BigEndian.Uint16(ext[:])), nil
	case 127:
		var ext [8]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return 0, err
		}
		// Compare as uint64: a value ≥ 2^63 would go negative through int()
		// and panic in make() before any later cap could see it.
		n := binary.BigEndian.Uint64(ext[:])
		if n > uint64(maxWebSocketFrame) {
			return 0, fmt.Errorf("websocket frame of %d bytes exceeds the %d byte limit", n, maxWebSocketFrame)
		}
		return int(n), nil
	default:
		return int(lengthByte), nil
	}
}
