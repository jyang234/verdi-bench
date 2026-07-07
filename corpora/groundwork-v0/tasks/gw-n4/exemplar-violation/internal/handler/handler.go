// Package handler is eventsvc's HTTP entry point.
package handler

import (
	"encoding/json"
	"net/http"

	"example.com/eventsvc/internal/notify"
	"example.com/eventsvc/internal/store"
)

// Server exposes the subscriber endpoints.
type Server struct {
	notifier *notify.Notifier
	store    *store.Store
}

// New returns a Server over n and st.
func New(n *notify.Notifier, st *store.Store) *Server { return &Server{notifier: n, store: st} }

// Get handles GET /subscribers/{id}: a read from the store, no publish.
func (s *Server) Get(w http.ResponseWriter, r *http.Request) {
	sub, ok := s.store.Get(r.PathValue("id"))
	if !ok {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(sub)
}

// Create handles POST /subscribers/{id}: it publishes a statically-named event,
// reaching only a resolvable publish (the clean contrast to a dynamic one).
func (s *Server) Create(w http.ResponseWriter, r *http.Request) {
	if err := s.notifier.Created(r.Context(), map[string]string{"id": r.PathValue("id")}); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

// Publish handles POST /publish/{id}: it emits an event whose name comes from the
// request under an app-scoped prefix, still reaching the dynamic publish
// boundary (the prefixed name is non-constant). A plausible alternative shape.
func (s *Server) Publish(w http.ResponseWriter, r *http.Request) {
	event := "app." + r.URL.Query().Get("event")
	if err := s.notifier.Emit(r.Context(), event, map[string]string{"id": r.PathValue("id")}); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusAccepted)
}
