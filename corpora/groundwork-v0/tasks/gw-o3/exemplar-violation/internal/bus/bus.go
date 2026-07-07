// Package bus is pubsvc's event bus.
package bus

// published records every published event in call order.
var published []string

// Publish appends event to the bus (the obligation's before anchor).
func Publish(event string) { published = append(published, event) }

// Published returns the published events in call order.
func Published() []string { return published }

// Reset clears the published events.
func Reset() { published = nil }
