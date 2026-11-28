package delivery

import (
	"errors"
	"io"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestReadSSE(t *testing.T) {
	errStop := errors.New("stop")
	big := strings.Repeat("x", 5<<20)
	tests := []struct {
		name    string
		input   string
		stopOn  string // onEvent returns errStop for an event with this name
		lines   int
		retries []time.Duration
		events  []sseEvent
		err     error
	}{
		{
			name:    "retry",
			input:   "retry: 3000\n\n",
			lines:   2,
			retries: []time.Duration{3 * time.Second},
		},
		{
			name:  "ping comment",
			input: ": ping\n",
			lines: 1,
		},
		{
			name:   "put with id and data",
			input:  "event: put\nid: 42\ndata: {\"flags\":{}}\n\n",
			lines:  4,
			events: []sseEvent{{name: "put", data: `{"flags":{}}`, id: "42"}},
		},
		{
			name:   "multi-line data",
			input:  "data: a\ndata: b\ndata:c\n\n",
			lines:  4,
			events: []sseEvent{{name: "message", data: "a\nb\nc"}},
		},
		{
			name:   "CRLF endings",
			input:  "event: put\r\nid: 7\r\ndata: x\r\n\r\n",
			lines:  4,
			events: []sseEvent{{name: "put", data: "x", id: "7"}},
		},
		{
			name:   "unknown event name passed through",
			input:  "event: mystery\ndata: 1\n\n",
			lines:  3,
			events: []sseEvent{{name: "mystery", data: "1"}},
		},
		{
			name:  "non-numeric retry ignored",
			input: "retry: x\n\n",
			lines: 2,
		},
		{
			name:   "unterminated final event discarded",
			input:  "data: a\n\nevent: put\ndata: b\n",
			lines:  4,
			events: []sseEvent{{name: "message", data: "a"}},
		},
		{
			name:   "onEvent error stops reading",
			input:  "event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n",
			stopOn: "revoked",
			lines:  3,
			events: []sseEvent{{name: "revoked", data: "{}"}},
			err:    errStop,
		},
		{
			name:   "state resets after dispatch",
			input:  "event: put\nid: 1\ndata: a\n\ndata: b\n\n",
			lines:  6,
			events: []sseEvent{{name: "put", data: "a", id: "1"}, {name: "message", data: "b"}},
		},
		{
			name:   "blank line without data dispatches nothing",
			input:  "event: put\n\ndata: a\n\n",
			lines:  4,
			events: []sseEvent{{name: "message", data: "a"}},
		},
		{
			name:   "id with NUL ignored",
			input:  "id: a\x00b\ndata: x\n\n",
			lines:  3,
			events: []sseEvent{{name: "message", data: "x"}},
		},
		{
			name:   "line without colon is a field with empty value",
			input:  "data\ndata\n\n",
			lines:  3,
			events: []sseEvent{{name: "message", data: "\n"}},
		},
		{
			name:   "only one leading space removed and unknown field ignored",
			input:  "foo: bar\ndata:  two\n\n",
			lines:  3,
			events: []sseEvent{{name: "message", data: " two"}},
		},
		{
			name:   "5 MB data line read whole",
			input:  "event: put\ndata: " + big + "\n\n",
			lines:  3,
			events: []sseEvent{{name: "put", data: big}},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var (
				lines   int
				retries []time.Duration
				events  []sseEvent
			)
			err := readSSE(strings.NewReader(tt.input),
				func() { lines++ },
				func(d time.Duration) { retries = append(retries, d) },
				func(e sseEvent) error {
					events = append(events, e)
					if tt.stopOn != "" && e.name == tt.stopOn {
						return errStop
					}
					return nil
				})
			want := tt.err
			if want == nil {
				want = io.EOF
			}
			if !errors.Is(err, want) {
				t.Errorf("err = %v, want %v", err, want)
			}
			if lines != tt.lines {
				t.Errorf("onLine calls = %d, want %d", lines, tt.lines)
			}
			if !reflect.DeepEqual(retries, tt.retries) {
				t.Errorf("retries = %v, want %v", retries, tt.retries)
			}
			if len(events) != len(tt.events) {
				t.Fatalf("got %d events, want %d", len(events), len(tt.events))
			}
			for i := range events {
				if events[i] != tt.events[i] {
					t.Errorf("event %d = {%q, %.40q, %q}, want {%q, %.40q, %q}", i,
						events[i].name, events[i].data, events[i].id,
						tt.events[i].name, tt.events[i].data, tt.events[i].id)
				}
			}
		})
	}
}

func TestReadSSEReadError(t *testing.T) {
	errRead := errors.New("boom")
	r := io.MultiReader(strings.NewReader(": ping\n"), &failingReader{err: errRead})
	err := readSSE(r, func() {}, func(time.Duration) {}, func(sseEvent) error { return nil })
	if err != errRead {
		t.Errorf("err = %v, want %v", err, errRead)
	}
}

type failingReader struct{ err error }

func (f *failingReader) Read([]byte) (int, error) { return 0, f.err }
