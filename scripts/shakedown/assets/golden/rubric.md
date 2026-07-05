# Code-task judging rubric (shakedown)

You are comparing two responses to a coding task. Judge **correctness first**:
the response whose holdout tests pass is more correct than one whose holdout
tests fail. If the diffs are empty or uninformative, decide entirely on the
holdout results. If both responses have identical holdout results and diffs,
return TIE.

## Output contract — READ CAREFULLY

Respond with **exactly one JSON object and nothing else** — no prose, no
markdown fences, no preamble. The object must be:

```
{"winner": "1" | "2" | "TIE" | "CANT_JUDGE",
 "reason": "<one short sentence>",
 "evidence": [{"kind": "holdout", "response": 1, "ref": "<assertion id>"}],
 "confidence": <number between 0 and 1>}
```

Rules:
- If `winner` is `"1"` or `"2"`, `evidence` MUST contain at least one item that
  cites a locator: for a holdout use `{"kind":"holdout","response":<1 or 2>,"ref":"<assertion id such as h1>"}`;
  for a diff use `{"kind":"diff","response":<1 or 2>,"hunk":"<a line from the diff>"}`.
- `"TIE"` and `"CANT_JUDGE"` need no evidence.
- Use `"CANT_JUDGE"` only if you genuinely cannot decide.
- Output the JSON object only.
