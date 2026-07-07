// Package store is eventsvc's read model: an in-memory set of subscribers the
// Get route reads. It performs no publish and reaches no blind spot.
package store

// Subscriber is one registered subscriber.
type Subscriber struct {
	ID   string
	Name string
}

// Store holds subscribers.
type Store struct{ subs map[string]Subscriber }

// New returns a Store with two seeded subscribers.
func New() *Store {
	return &Store{subs: map[string]Subscriber{
		"s1": {ID: "s1", Name: "Alice"},
		"s2": {ID: "s2", Name: "Bob"},
	}}
}

// Get returns a subscriber by id.
func (s *Store) Get(id string) (Subscriber, bool) {
	sub, ok := s.subs[id]
	return sub, ok
}
