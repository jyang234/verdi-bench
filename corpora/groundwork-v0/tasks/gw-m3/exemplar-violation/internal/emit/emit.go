// Package emit holds the activity-emitter seam. Emitter has three
// implementations; main wires exactly one live per route. A caller cannot tell
// from the interface alone whether Emit reaches the bus — only the wired
// implementation decides that.
package emit

import (
	"context"
	"log"

	"example.com/feedsvc/internal/bus"
)

// Emitter records an activity occurrence against a feed item id.
type Emitter interface {
	Emit(ctx context.Context, id string) error
}

// LogEmitter writes the activity to the process log (no external effect).
type LogEmitter struct{}

// NewLog returns a LogEmitter.
func NewLog() *LogEmitter { return &LogEmitter{} }

// Emit writes the activity for id to the log.
func (e *LogEmitter) Emit(_ context.Context, id string) error {
	log.Printf("activity: %s", id)
	return nil
}

// NopEmitter emits nothing (used where an emitter is optional).
type NopEmitter struct{}

// NewNop returns a NopEmitter.
func NewNop() *NopEmitter { return &NopEmitter{} }

// Emit does nothing.
func (NopEmitter) Emit(_ context.Context, _ string) error { return nil }

// BusEmitter publishes the activity to the event bus (a bus PUBLISH per Emit).
type BusEmitter struct{ bus *bus.Bus }

// NewBus returns a BusEmitter over b.
func NewBus(b *bus.Bus) *BusEmitter { return &BusEmitter{bus: b} }

// Emit publishes an activity event on the bus.
func (e *BusEmitter) Emit(ctx context.Context, id string) error {
	return e.bus.Publish(ctx, "feed.activity", id)
}
