# Codex and Claude Code adapters

## Codex

Use a fresh-context subagent mechanism when available. A worker must receive no parent transcript, reasoning summary, or sibling output. Create a separate Git worktree or copied workspace first; subagents may otherwise share a filesystem. Retain the worker/session identifier so the selected worker can continue without flushing during exploitation.

If only CLI execution is available, inspect the installed `codex exec` and resume commands rather than assuming flags. Store session IDs outside inherited workspaces.

## Claude Code

Use a fresh subagent or non-resumed CLI session for every exploration branch and a separate worktree/copy. Continue the selected branch with the same session when possible. A summarized or branched parent conversation is not a flush.

## Shared worker brief

Give each worker the same task facts, evaluator, validity constraints, editable scope, and inherited checkpoint. Do not disclose total or remaining budget. A concise brief is:

> Work independently in this isolated workspace. Improve the stated metric using the frozen evaluator and constraints. Preserve the inherited verified checkpoint. Explore and test alternatives, save exact candidate artifacts and evaluator evidence, and return their identifiers. Do not modify the evaluator or inspect sibling workspaces.

The coordinator owns budgets, schedules, ledgers, selection, final verification, and integration. If the runtime lacks exact usage counters, record conservative estimates and uncertainty; do not describe them as measured or as a hard billing limit.
