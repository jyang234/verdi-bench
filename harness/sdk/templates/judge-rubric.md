# Code-task judging rubric

You are comparing two responses to a coding task. Judge **correctness first**:
the response whose holdout tests pass is more correct than one whose holdout
tests fail. If the diffs are empty or uninformative, decide entirely on the
holdout results.

If both responses have identical holdout results and diffs, neither is better —
call it a tie. Abstain only if you genuinely cannot decide.

> The response format is supplied by the harness — it tells you exactly which
> JSON object to return. This rubric covers only *how to judge*, not how to reply.
