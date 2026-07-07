// Package bus is feedsvc's outbound event-bus seam. Publish is the async egress
// boundary; .flowmap.yaml classifies bus.Publish as busPublish so a publish
// surfaces as a bus PUBLISH effect. A real deployment backs this with a broker
// client; the body here is a static stand-in that is only ever analyzed, never
// run under the gate.
package bus

import "context"

// Bus is the publish seam.
type Bus struct{}

// New returns a Bus.
func New() *Bus { return &Bus{} }

// Publish emits payload on topic (an outbound-async boundary effect). The topic
// is the call-site constant the boundary extractor reads.
func (b *Bus) Publish(ctx context.Context, topic, payload string) error {
	return nil
}
