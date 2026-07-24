# Merge Conflict Resolution Rules

You are resolving git merge conflicts between our `base-merged` branch
and an external fork. Follow these rules exactly. Do not deviate.

1. Resolve ONE conflicted file per commit. Never bulk-resolve.
2. For each file, first classify the conflict:
   - ADDITIVE: both sides add unrelated functionality → keep both
   - COMPETING: both sides change the same logic → prefer OUR side
     unless the fork's change is a bugfix to code we didn't touch
   - AMBIGUOUS: you're not confident → do NOT guess. Leave the
     conflict markers in place and flag it in your summary instead.
3. Never delete tests. If both sides have tests for the same thing,
   keep both, renaming if needed to avoid collisions.
4. Never modify files outside the conflicted list for this merge.
5. Never resolve docs/*, README.md, or CHANGELOG.md content conflicts
   automatically — always flag these for human review.
6. After resolving a file, write one sentence explaining the
   decision and why, appended to CONFLICT_LOG.md.
7. Do not run `git cherry-pick --continue`, `git merge --continue`,
   or any command that finalizes the merge. Stop after resolving
   individual files and producing the log.
