# Stock Competition agent instructions

Preserve the user's portfolio databases, account history, saved plans, secrets,
and known-working desktop builds. The website and daily assistant are paper
trading only. Do not place real trades or silently change strategy formulas.

## "idle work" trigger

When the user says **idle work** for this project:

1. Inspect the current repository state and recent project goals. Brainstorm
   several useful improvements and choose one bounded, high-value task that can
   be completed and verified in the current run.
2. State the chosen task and why it is next, then implement it. Do not stop at
   a brainstorm unless a required external choice prevents the work.
3. Reuse existing systems, preserve unrelated changes, and protect runtime data.
4. Run focused verification, report what changed, what passed, and what remains.
5. Do not interpret the phrase as permission to deploy, incur costs, execute
   trades, commit, push, or modify live account data.

The coordinator in `coordinator/` has its own `AGENTS.md` and role rules; keep
those instructions intact when working there.
