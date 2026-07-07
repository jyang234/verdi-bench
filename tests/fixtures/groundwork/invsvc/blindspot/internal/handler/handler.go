// Package handler is alertsvc's HTTP entry point. The Notify route reaches a
// statically-named publish, but its payload is rendered through a reflect call,
// so a reachability question rooted at Notify runs into the reflect frontier —
// exactly the case a must_not_reach "no path found" verdict must NOT report as a
// proof. The grader plugin maps the resulting caution to abstain.
package handler

import (
	"net/http"

	"example.com/alertsvc/internal/relay"
)

// Server exposes the relay endpoints.
type Server struct {
	relay *relay.Relay
}

// New returns a Server over r.
func New(r *relay.Relay) *Server { return &Server{relay: r} }

// Emit handles POST /emit: it publishes an event whose name comes from the
// request, reaching the dynamic publish boundary.
func (s *Server) Emit(w http.ResponseWriter, r *http.Request) {
	event := r.URL.Query().Get("event")
	if err := s.relay.Dynamic(r.Context(), event, map[string]string{"id": r.PathValue("id")}); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusAccepted)
}

// Notify handles POST /notify/{id}: it publishes a statically-named event, but
// renders the payload reflectively — so this entrypoint reaches a reflect blind
// spot and the graph cannot prove what lies beyond it.
func (s *Server) Notify(w http.ResponseWriter, r *http.Request) {
	if err := s.relay.Created(r.Context(), map[string]string{"id": r.PathValue("id")}); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
}
