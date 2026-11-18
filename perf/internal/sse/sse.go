// Package sse reads Server-Sent Events streams.
package sse

import (
	"bufio"
	"context"
	"errors"
	"io"
	"strings"
)

// Event is one dispatched event. Name is "message" when the stream sets none.
type Event struct{ Name, Data, ID string }

// Read reads r until it ends and calls onEvent for every event and onComment
// (if not nil) for every comment line, such as the server's `: ping`
// heartbeat. The framing is parse_sse in scripts/propagation_test.py: an
// event is dispatched on a blank line if it has a data line, `retry` and
// unknown fields are ignored, and an event cut off by the end of the stream
// is dropped. Lines end with LF or CRLF.
//
// Read returns nil when the stream ends and ctx.Err() once ctx is done;
// cancelling the request whose body r is unblocks a pending read.
func Read(ctx context.Context, r io.Reader, onEvent func(Event), onComment func()) error {
	br := bufio.NewReader(r)
	name, id := "message", ""
	var data []string
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		line, err := br.ReadString('\n')
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")
		switch {
		case line == "":
			if data != nil {
				onEvent(Event{Name: name, Data: strings.Join(data, "\n"), ID: id})
			}
			name, id, data = "message", "", nil
		case strings.HasPrefix(line, ":"):
			if onComment != nil {
				onComment()
			}
		default:
			field, value, _ := strings.Cut(line, ":")
			value = strings.TrimPrefix(value, " ")
			switch field {
			case "event":
				name = value
			case "data":
				data = append(data, value)
			case "id":
				id = value
			}
		}
	}
}
