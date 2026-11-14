package delivery

import (
	"bufio"
	"bytes"
	"errors"
	"io"
	"strconv"
	"strings"
	"time"
)

// sseEvent is one dispatched server-sent event. name is "message" when the
// stream sent no `event` field.
type sseEvent struct {
	name, data, id string
}

// readSSE parses a text/event-stream from r until it ends or onEvent fails.
// onLine is called for every line, comments included, so a caller can reset
// its dead-connection timer. onRetry receives a valid `retry` field. At the end
// of the stream it returns io.EOF and discards an unterminated event; it
// returns the error of onEvent, or a read error, unchanged.
func readSSE(r io.Reader, onLine func(), onRetry func(time.Duration), onEvent func(sseEvent) error) error {
	br := bufio.NewReaderSize(r, 64<<10)
	var (
		name, id string
		data     []string
		seen     bool
	)
	for {
		line, err := readLine(br)
		if err != nil {
			return err
		}
		onLine()
		if len(line) == 0 {
			if seen {
				if name == "" {
					name = "message"
				}
				if err := onEvent(sseEvent{name: name, data: strings.Join(data, "\n"), id: id}); err != nil {
					return err
				}
			}
			name, id, data, seen = "", "", nil, false
			continue
		}
		if line[0] == ':' {
			continue
		}
		field, value := line, []byte(nil)
		if i := bytes.IndexByte(line, ':'); i >= 0 {
			field, value = line[:i], line[i+1:]
			value = bytes.TrimPrefix(value, []byte(" "))
		}
		switch string(field) {
		case "event":
			name = string(value)
		case "data":
			data = append(data, string(value))
			seen = true
		case "id":
			if bytes.IndexByte(value, 0) < 0 {
				id = string(value)
			}
		case "retry":
			if ms, ok := parseDigits(value); ok {
				onRetry(time.Duration(ms) * time.Millisecond)
			}
		}
	}
}

// readLine returns the next line without its "\n" or "\r\n" ending. A line
// longer than the buffer is assembled from fragments. A final line without an
// ending is dropped and io.EOF is returned.
func readLine(br *bufio.Reader) ([]byte, error) {
	frag, err := br.ReadSlice('\n')
	if err == nil {
		return trimEOL(frag), nil
	}
	var buf []byte
	for errors.Is(err, bufio.ErrBufferFull) {
		buf = append(buf, frag...)
		frag, err = br.ReadSlice('\n')
	}
	if err != nil {
		return nil, err
	}
	return trimEOL(append(buf, frag...)), nil
}

func trimEOL(b []byte) []byte {
	b = b[:len(b)-1]
	if n := len(b); n > 0 && b[n-1] == '\r' {
		b = b[:n-1]
	}
	return b
}

// parseDigits accepts a non-empty string of ASCII digits only.
func parseDigits(b []byte) (int64, bool) {
	if len(b) == 0 {
		return 0, false
	}
	for _, c := range b {
		if c < '0' || c > '9' {
			return 0, false
		}
	}
	ms, err := strconv.ParseInt(string(b), 10, 64)
	if err != nil {
		return 0, false
	}
	return ms, true
}
