// Package audit is pubsvc's approval audit log.
package audit

// log records every audited event in call order.
var log []string

// Write appends event to the audit log (the obligation's require anchor).
func Write(event string) { log = append(log, event) }

// Events returns the audited events in call order.
func Events() []string { return log }

// Reset clears the audit log.
func Reset() { log = nil }
