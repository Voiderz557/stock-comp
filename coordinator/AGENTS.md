# Coordinator agent instructions

These instructions are shared by the Cursor implementer and the Codex reviewer.
They are copied into every isolated task worktree.

## Roles

- Cursor implements the task and may edit project code.
- Codex reviews the actual git diff and test evidence only. Codex must not edit files.
- The coordinator never commits, pushes, merges, publishes, or places trades.

## Acceptance

Follow the task statement and the numbered acceptance criteria exactly.
If a criterion cannot be met, say so; do not invent extra scope.

## Safety

Do not read, copy, or write:

- `paper_trading_data/`, `*.sqlite3` portfolio ledgers
- `dist/`, packaged `.exe` builds
- `.env`, API keys, tokens, `auth.json`, credential files
- `%LOCALAPPDATA%\StockComp\` live user data

Do not configure paid API-key fallbacks or enable extra spending.

## Reviewer output

Codex must print a single JSON object and nothing else:

```json
{
  "status": "approved|changes_requested|blocked|incomplete",
  "summary": "one paragraph",
  "tests_reviewed": true,
  "findings": [
    {
      "severity": "blocker|major|minor",
      "title": "short title",
      "action": "what Cursor should do"
    }
  ]
}
```

Missing, non-JSON, or schema-invalid output is incomplete — never approved.
`approved` is allowed only when `findings` contains no `blocker` items.
