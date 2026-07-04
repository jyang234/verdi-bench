"""Contamination sentinel — training-set membership detection [EVAL-10].

Three independent detection channels, each honest about what it can prove:
deterministic cutoff dating (:mod:`.dating`), planted canaries
(:mod:`.canary`) surfaced by memory probes (:mod:`.probe`), and
solution-overlap fingerprints (:mod:`.overlap`). :mod:`.summary` joins them
into the per-arm summary every render discloses. The deterministic detectors
import no LLM client (import-linter enforced [AC-6]); :mod:`.probe` is the
story's only LLM-touching module.
"""
