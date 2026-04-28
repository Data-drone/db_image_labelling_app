# LAKEBASE_AUTO_PROVISION switch — design

Date: 2026-04-28
Author: Data-drone (brian.law)
Status: Approved, ready for implementation plan

## Problem

The CV Explorer app auto-provisions its Lakebase PostgreSQL project on
startup (`backend/lakebase.py:ensure_lakebase_project`). For standalone
deployments this is the right default — the app bootstraps its own
state store with zero operator action.

When the app is embedded in a larger Databricks Asset Bundle (DAB),
the bundle wants to *own* the Lakebase resource: create it, grant
roles, enable Lakehouse Sync, tear it down. Two owners fighting over
the same resource is a recipe for drift and duplicate creation
attempts, and there is currently no way to tell the app "don't create,
just connect".

## Goal

Add a single opt-out environment variable, `LAKEBASE_AUTO_PROVISION`,
that DAB-managed deployments can set to `false`. When unset (default),
behavior is unchanged — the app auto-provisions as it does today.

## Non-goals

- Supporting multiple Lakebase projects per app
- Adding a provider abstraction / pluggable backends
- Refactoring the existing SDK call sites
- Adding tests (repo has no test infrastructure; out of scope)
- Changing Postgres role / permission management (already external)

## Design

### Config

One new env var, read in `backend/lakebase.py` module scope next to
the two existing ones (`LAKEBASE_PROJECT_ID`, `LAKEBASE_DISPLAY_NAME`):

```python
LAKEBASE_AUTO_PROVISION = (
    os.environ.get("LAKEBASE_AUTO_PROVISION", "true").lower() != "false"
)
```

Default `"true"` preserves current behavior. Uses the same
`"true"/"false"` string-boolean convention as `USE_LAKEBASE` in
`backend/main.py:47`.

### Behavior

In `ensure_lakebase_project()` (backend/lakebase.py:47-67), the `except`
branch currently creates the project unconditionally. Add a guard:

```python
except Exception:
    if not LAKEBASE_AUTO_PROVISION:
        raise RuntimeError(
            f"Lakebase project '{LAKEBASE_PROJECT_ID}' not found "
            f"and LAKEBASE_AUTO_PROVISION=false. "
            f"Pre-create the project (e.g. via DAB) or unset the flag."
        )
    log.info("Lakebase project not found, creating: %s", LAKEBASE_PROJECT_ID)
    # ... existing create_project call unchanged
```

Three runtime scenarios:

| `LAKEBASE_AUTO_PROVISION` | Project exists | Outcome |
|---|---|---|
| unset / `true` | yes | Connects (unchanged) |
| unset / `true` | no | Creates then connects (unchanged) |
| `false` | yes | Connects |
| `false` | no | RuntimeError with remediation hint |

### Documentation

Update `README.md`:

1. *Env var table* (around line 80) — add row:
   ```
   | LAKEBASE_AUTO_PROVISION | true | Set to "false" to require a
   pre-existing Lakebase project (e.g. when managed by a Databricks
   Asset Bundle). If the project is missing at startup, the app will
   exit with an error instead of creating it. |
   ```

2. *Prose* — soften "auto-provisioned" wording at lines 16, 45, 65
   to "auto-provisioned by default". No other prose changes.

3. *New section* near the end: "Using with Databricks Asset Bundles" —
   brief note on setting `LAKEBASE_AUTO_PROVISION=false` and why,
   with a pointer to the DAB docs on Lakebase resource management.

### Backward compatibility

100%. Existing deployments that don't set the var get the current
behavior. New feature is strictly opt-in.

### Error handling

One new error path (the `raise RuntimeError` above). Message includes
the project ID, the flag state, and remediation. Raised from
`init_lakebase()` call chain, caught by FastAPI's startup — the app
will fail to start, which is the desired behavior for a misconfigured
deploy. Matches the existing pattern: other Lakebase errors in this
file also raise `RuntimeError` (lines 77, 84).

### Testing

No test infrastructure exists in the repo. Manual verification:

| Scenario | Expected |
|---|---|
| Unset, project exists | Connects (current behavior) |
| Unset, project missing | Creates, connects (current behavior) |
| `=false`, project exists | Connects |
| `=false`, project missing | Startup fails with the new error message |
| `=true`, project missing | Creates, connects (explicit form of default) |

Verified manually against the `cv-explorer` Lakebase project on the
user's workspace before PR merge.

## Implementation

Small, self-contained. One code file, one doc file, two commits on a
feature branch.

### Files to touch

- `backend/lakebase.py` — add module-level constant and guard clause
  (~8 lines added)
- `README.md` — env table row, prose softening, new DAB section
  (~20 lines added)

### Branch & commits

- Branch: `feat/lakebase-auto-provision-switch`
- Commit 1: "Add LAKEBASE_AUTO_PROVISION env var to opt out of
  startup provisioning" — code change
- Commit 2: "Document LAKEBASE_AUTO_PROVISION and DAB usage" —
  README update
- Author: `Data-drone <bpl.law@gmail.com>` (pre-commit hook installed
  in this worktree now blocks the Andy attribution issue that
  affected phase-2 merges)

### PR shape

- Title: "Add LAKEBASE_AUTO_PROVISION env var for DAB-managed
  deployments"
- Body: explains use case (DAB ownership), links to Databricks Apps
  Lakebase resource docs, confirms zero breaking changes (default
  preserves current behavior), lists manual verification scenarios

## Deferred / future

- Same mechanism could later gate *role* creation if the app ever
  auto-creates Postgres roles (it doesn't today)
- Could add an analogous `LAKEBASE_REQUIRE_EXISTING` to also skip the
  `get_project` probe for faster startup, but that's a second knob
  that duplicates the same intent. YAGNI.
