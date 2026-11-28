package sse

import (
	"context"
	"errors"
	"io"
	"reflect"
	"strings"
	"testing"
)

func TestReadParsesEventsAndComments(t *testing.T) {
	stream := "retry: 1000\r\n\r\n" +
		"id: e1\r\nevent: put\r\ndata: {\"a\":\r\ndata:  1}\r\n\r\n" +
		": ping\n\n" +
		"data: plain\n\n" +
		"event: empty\n\n" +
		"id: e3\nevent: revoked\ndata\n\n" +
		"event: unterminated\ndata: x\n"
	var events []Event
	comments := 0
	err := Read(context.Background(), strings.NewReader(stream),
		func(e Event) { events = append(events, e) }, func() { comments++ })
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	want := []Event{
		{Name: "put", Data: "{\"a\":\n 1}", ID: "e1"},
		{Name: "message", Data: "plain"},
		{Name: "revoked", Data: "", ID: "e3"},
	}
	if !reflect.DeepEqual(events, want) {
		t.Errorf("events = %#v, want %#v", events, want)
	}
	if comments != 1 {
		t.Errorf("comments = %d, want 1", comments)
	}
}

func TestReadAllowsNoCommentCallback(t *testing.T) {
	n := 0
	err := Read(context.Background(), strings.NewReader(": ping\ndata: x\n\n"), func(Event) { n++ }, nil)
	if err != nil || n != 1 {
		t.Fatalf("Read = %v with %d events, want nil with 1", err, n)
	}
}

func TestReadStopsWhenContextIsDone(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	r, w := io.Pipe()
	go func() {
		_, _ = io.WriteString(w, "data: first\n\n")
	}()
	err := Read(ctx, r, func(Event) {
		cancel()
		_ = w.Close() // a cancelled request closes its body the same way
	}, nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Read = %v, want context.Canceled", err)
	}
}

func TestReadReturnsReadErrors(t *testing.T) {
	boom := errors.New("boom")
	r := io.MultiReader(strings.NewReader("data: x\n\n"), &failingReader{boom})
	if err := Read(context.Background(), r, func(Event) {}, nil); !errors.Is(err, boom) {
		t.Fatalf("Read = %v, want %v", err, boom)
	}
}

type failingReader struct{ err error }

func (f *failingReader) Read([]byte) (int, error) { return 0, f.err }
