# LAKEBASE_AUTO_PROVISION Switch — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an opt-in `LAKEBASE_AUTO_PROVISION=false` env var to `backend/lakebase.py` so DAB-managed deployments can own the Lakebase project externally; default `true` preserves current behavior. Document in `README.md` and open a PR on the upstream repo.

**Architecture:** Single env var read at module scope. One guard clause in `ensure_lakebase_project()` raises `RuntimeError` with remediation hint when the project is missing and the flag is `false`. Zero breaking changes.

**Tech Stack:** Python 3.11, FastAPI, databricks-sdk. No test framework exists in the repo — manual smoke verification only.

**Design reference:** `docs/plans/2026-04-28-lakebase-auto-provision-switch-design.md`

---

## Pre-flight

Before Task 1, confirm the working directory is clean and on main:

```bash
cd /workspace/group/cv-react-deploy
git status         # expect: nothing to commit, working tree clean
git branch --show-current   # expect: main
git log --format="%h %an %s" -1   # expect: design-doc commit 81dd3b1 as Data-drone
```

If dirty, stash or resolve before proceeding.

---

## Task 1: Create feature branch

**Files:** none (git plumbing only)

**Step 1: Create and checkout branch**

Run:
```bash
git checkout -b feat/lakebase-auto-provision-switch
```
Expected: `Switched to a new branch 'feat/lakebase-auto-provision-switch'`

**Step 2: Verify branch is on latest main**

Run:
```bash
git log --oneline -3
```
Expected: top commit is `81dd3b1` (design doc). If not, abort and reconcile.

**Step 3: No commit yet** — branch exists, empty of changes.

---

## Task 2: Add env var constant to `backend/lakebase.py`

**Files:**
- Modify: `backend/lakebase.py:26-28` (insert new line after `LAKEBASE_DISPLAY_NAME`)

**Step 1: Edit the module-level config section**

Current content at lines 26-28:
```python
LAKEBASE_PROJECT_ID = os.environ.get("LAKEBASE_PROJECT_ID", "cv-explorer")
LAKEBASE_DISPLAY_NAME = os.environ.get("LAKEBASE_DISPLAY_NAME", "CV Explorer")
TOKEN_REFRESH_INTERVAL = 20 * 60  # 20 minutes (tokens typically expire in ~1 hour)
```

Replace with:
```python
LAKEBASE_PROJECT_ID = os.environ.get("LAKEBASE_PROJECT_ID", "cv-explorer")
LAKEBASE_DISPLAY_NAME = os.environ.get("LAKEBASE_DISPLAY_NAME", "CV Explorer")
# When "false", the app will NOT create a Lakebase project if one does
# not exist; instead it raises on startup. Use this when a Databricks
# Asset Bundle (or other external harness) owns the Lakebase resource.
LAKEBASE_AUTO_PROVISION = (
    os.environ.get("LAKEBASE_AUTO_PROVISION", "true").lower() != "false"
)
TOKEN_REFRESH_INTERVAL = 20 * 60  # 20 minutes (tokens typically expire in ~1 hour)
```

Use the `Edit` tool with the full old_string / new_string above.

**Step 2: Verify the file parses**

Run:
```bash
python3 -c "import ast; ast.parse(open('backend/lakebase.py').read()); print('OK')"
```
Expected: `OK`

**Step 3: Verify the constant is truthy by default**

Run:
```bash
python3 -c "import importlib.util, sys; spec = importlib.util.spec_from_file_location('lb', 'backend/lakebase.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('DEFAULT:', mod.LAKEBASE_AUTO_PROVISION)"
```
Expected: `DEFAULT: True`

**Step 4: Verify `LAKEBASE_AUTO_PROVISION=false` flips it**

Run:
```bash
LAKEBASE_AUTO_PROVISION=false python3 -c "import importlib.util; spec = importlib.util.spec_from_file_location('lb', 'backend/lakebase.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('FALSE:', mod.LAKEBASE_AUTO_PROVISION)"
```
Expected: `FALSE: False`

Also verify case-insensitivity:
```bash
LAKEBASE_AUTO_PROVISION=FALSE python3 -c "import importlib.util; spec = importlib.util.spec_from_file_location('lb', 'backend/lakebase.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('CAPS:', mod.LAKEBASE_AUTO_PROVISION)"
```
Expected: `CAPS: False`

**Step 5: Do NOT commit yet** — Task 3 also edits this file, we commit them together.

---

## Task 3: Add the guard clause in `ensure_lakebase_project`

**Files:**
- Modify: `backend/lakebase.py:54-59` (inside `ensure_lakebase_project`'s `except` branch)

**Step 1: Edit the except branch**

Current content (lines 54-59 after Task 2's insertion, use anchoring text to find exactly):
```python
    try:
        project = w.postgres.get_project(name=project_name)
        log.info("Connected to existing Lakebase project: %s", project.name)
        return project
    except Exception:
        log.info("Lakebase project not found, creating: %s", LAKEBASE_PROJECT_ID)
```

Replace with:
```python
    try:
        project = w.postgres.get_project(name=project_name)
        log.info("Connected to existing Lakebase project: %s", project.name)
        return project
    except Exception:
        if not LAKEBASE_AUTO_PROVISION:
            raise RuntimeError(
                f"Lakebase project '{LAKEBASE_PROJECT_ID}' not found "
                f"and LAKEBASE_AUTO_PROVISION=false. "
                f"Pre-create the project (e.g. via DAB) or unset the flag."
            )
        log.info("Lakebase project not found, creating: %s", LAKEBASE_PROJECT_ID)
```

**Step 2: Verify the file still parses**

Run:
```bash
python3 -c "import ast; ast.parse(open('backend/lakebase.py').read()); print('OK')"
```
Expected: `OK`

**Step 3: Verify the guard logic (unit-level, no network)**

Run:
```bash
python3 <<'PY'
import os, sys
os.environ['LAKEBASE_AUTO_PROVISION'] = 'false'
sys.path.insert(0, 'backend')
import lakebase

# Monkeypatch workspace client so get_project always raises (simulates
# missing project) and create_project would blow up loudly if reached.
class FakePostgres:
    def get_project(self, name): raise RuntimeError("not found")
    def create_project(self, **k): raise AssertionError("create_project should NOT be called when AUTO_PROVISION=false")
class FakeClient:
    postgres = FakePostgres()
lakebase._get_workspace_client = lambda: FakeClient()

try:
    lakebase.ensure_lakebase_project()
except RuntimeError as e:
    msg = str(e)
    assert "LAKEBASE_AUTO_PROVISION=false" in msg, f"missing flag hint: {msg}"
    assert "cv-explorer" in msg, f"missing project id: {msg}"
    print("GUARD TRIGGERED CORRECTLY:", msg)
else:
    raise SystemExit("ensure_lakebase_project should have raised")
PY
```
Expected: `GUARD TRIGGERED CORRECTLY: Lakebase project 'cv-explorer' not found and LAKEBASE_AUTO_PROVISION=false. Pre-create the project (e.g. via DAB) or unset the flag.`

**Step 4: Verify default still creates**

Run:
```bash
python3 <<'PY'
import os, sys
os.environ.pop('LAKEBASE_AUTO_PROVISION', None)
sys.path.insert(0, 'backend')
import importlib
if 'lakebase' in sys.modules:
    importlib.reload(sys.modules['lakebase'])
import lakebase
called = {'create': False}
class FakePostgres:
    def get_project(self, name): raise RuntimeError("not found")
    def create_project(self, **k):
        called['create'] = True
        class Op:
            def wait(self_inner):
                class P:
                    name = "projects/cv-explorer"
                return P()
        return Op()
class FakeClient:
    postgres = FakePostgres()
lakebase._get_workspace_client = lambda: FakeClient()

proj = lakebase.ensure_lakebase_project()
assert called['create'], "create_project should have been called under default"
print("DEFAULT PATH OK — created:", proj.name)
PY
```
Expected: `DEFAULT PATH OK — created: projects/cv-explorer`

**Step 5: Commit code change**

```bash
git add backend/lakebase.py
git commit -m "Add LAKEBASE_AUTO_PROVISION env var to opt out of startup provisioning

Default 'true' preserves current behavior. When set to 'false', the app
raises a RuntimeError on startup if the Lakebase project is missing,
instead of creating it. Intended for Databricks Asset Bundle deployments
where the bundle owns the Lakebase resource lifecycle."
```

Expected: commit created, `Data-drone` as author (pre-commit hook verifies).

**Step 6: Verify commit**

Run:
```bash
git log --format="%h %an <%ae> %s" -1
```
Expected: `<hash> Data-drone <bpl.law@gmail.com> Add LAKEBASE_AUTO_PROVISION env var ...`

---

## Task 4: Update README env var table

**Files:**
- Modify: `README.md:80-81` (insert new table row after `LAKEBASE_DISPLAY_NAME`)

**Step 1: Locate the env table**

Run:
```bash
sed -n '78,85p' README.md
```
Expected (verbatim):
```
| Variable | Default | Description |
|----------|---------|-------------|
| `DATABRICKS_APP_PORT` | `8000` | Port for the FastAPI server |
| `DEMO_VOLUME_PATH` | `/Volumes/brian_gen_ai/cv_explorer/demo_images` | Default demo volume (set in `app.yaml`) |
| `LAKEBASE_PROJECT_ID` | `cv-explorer` | Lakebase project identifier |
| `LAKEBASE_DISPLAY_NAME` | `CV Explorer` | Lakebase project display name |

```

**Step 2: Edit to add new row**

Replace the `LAKEBASE_DISPLAY_NAME` line with itself PLUS a new row below:

Old:
```
| `LAKEBASE_DISPLAY_NAME` | `CV Explorer` | Lakebase project display name |
```

New:
```
| `LAKEBASE_DISPLAY_NAME` | `CV Explorer` | Lakebase project display name |
| `LAKEBASE_AUTO_PROVISION` | `true` | Set to `"false"` to require a pre-existing Lakebase project (e.g. when managed by a Databricks Asset Bundle). If the project is missing at startup, the app exits with an error instead of creating it. |
```

**Step 3: Verify the table still renders**

Run:
```bash
sed -n '78,86p' README.md
```
Expected: table now has 6 data rows, the new `LAKEBASE_AUTO_PROVISION` row is last.

**Step 4: Do NOT commit yet** — Tasks 5 and 6 also edit README.

---

## Task 5: Soften "auto-provisioned" prose

**Files:**
- Modify: `README.md:16` (feature bullet)
- Modify: `README.md:45` (backend description)
- Modify: `README.md:65` (deployment steps)

**Step 1: Soften feature bullet (line 16)**

Old:
```
- **Lakebase integration**: auto-provisioned PostgreSQL with token refresh and Lakehouse Sync to Delta
```
New:
```
- **Lakebase integration**: auto-provisioned by default (opt out for DAB-managed deployments) with token refresh and Lakehouse Sync to Delta
```

**Step 2: Soften backend description (line 45)**

Old:
```
The FastAPI backend serves the React SPA as static files and provides the `/api/` endpoints. On startup it auto-provisions a Lakebase project with a background thread that refreshes database tokens every 20 minutes.
```
New:
```
The FastAPI backend serves the React SPA as static files and provides the `/api/` endpoints. On startup it auto-provisions a Lakebase project (by default — see `LAKEBASE_AUTO_PROVISION`) with a background thread that refreshes database tokens every 20 minutes.
```

**Step 3: Soften deployment step (line 65)**

Old:
```
4. On first boot, Lakebase is auto-provisioned and tables are created
```
New:
```
4. On first boot, Lakebase is auto-provisioned (unless `LAKEBASE_AUTO_PROVISION=false`) and tables are created
```

**Step 4: Verify all three changes**

Run:
```bash
grep -n "auto-provision" README.md
```
Expected: 3+ matches, all now mention the flag or "by default".

**Step 5: Do NOT commit yet** — Task 6 adds the DAB section.

---

## Task 6: Add "Using with Databricks Asset Bundles" section

**Files:**
- Modify: `README.md` (add new section before or after "Project Structure")

**Step 1: Locate insertion point**

Run:
```bash
grep -n "^## " README.md
```
Expected: lists the existing section headers. Identify a good spot — after the env var table section (likely "## Configuration" or similar), before "## Project Structure".

**Step 2: Add the section**

Insert the following new section just before `## Project Structure`:

```markdown
## Using with Databricks Asset Bundles

When deploying this app as part of a larger Databricks Asset Bundle
(DAB), the bundle typically owns the Lakebase project lifecycle
(create / grant roles / enable Lakehouse Sync / destroy). To prevent
the app from trying to create a duplicate project at startup, set:

```yaml
# app.yaml
env:
  - name: LAKEBASE_AUTO_PROVISION
    value: "false"
  - name: LAKEBASE_PROJECT_ID
    value: "<project-id-created-by-your-bundle>"
```

With `LAKEBASE_AUTO_PROVISION=false`, the app:

1. Looks up the Lakebase project named by `LAKEBASE_PROJECT_ID`
2. Connects if it exists
3. Exits with a clear error if it does not (instead of creating one)

See the [Databricks Apps → Lakebase resources
documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources#lakebase)
for how DAB declares Postgres project ownership and permissions.

```

**Step 3: Verify section added**

Run:
```bash
grep -n "Using with Databricks Asset Bundles" README.md
```
Expected: 1 match.

**Step 4: Commit all README changes**

```bash
git add README.md
git commit -m "Document LAKEBASE_AUTO_PROVISION and DAB usage

Adds env table entry, softens 'auto-provisioned' prose to note the
opt-out, and adds a 'Using with Databricks Asset Bundles' section
explaining how DAB-managed deployments should set the flag."
```

Expected: commit created, author `Data-drone`.

**Step 5: Verify both commits on branch**

Run:
```bash
git log --format="%h %an %s" origin/main..HEAD
```
Expected: exactly 2 commits on the branch:
- `<hash> Data-drone Document LAKEBASE_AUTO_PROVISION and DAB usage`
- `<hash> Data-drone Add LAKEBASE_AUTO_PROVISION env var ...`

---

## Task 7: Push branch to origin

**Files:** none (git plumbing)

**Step 1: Push with upstream tracking**

Run:
```bash
git push -u origin feat/lakebase-auto-provision-switch
```
Expected: output includes the remote URL for opening a PR.

**Step 2: Verify on remote**

Run:
```bash
git ls-remote --heads origin feat/lakebase-auto-provision-switch
```
Expected: single ref, hash matches local `HEAD`.

---

## Task 8: Open the PR

**Files:** none (GitHub API via `gh`)

**Step 1: Create PR**

Run:
```bash
gh pr create \
  --repo Data-drone/db_image_labelling_app \
  --base main \
  --head feat/lakebase-auto-provision-switch \
  --title "Add LAKEBASE_AUTO_PROVISION env var for DAB-managed deployments" \
  --body "$(cat <<'EOF'
## Summary

Adds a single opt-out environment variable, `LAKEBASE_AUTO_PROVISION`,
that lets Databricks Asset Bundle (DAB) deployments tell the app
*not* to auto-create a Lakebase project at startup.

- Default `"true"` — 100% backward compatible, behaves like today
- Set `"false"` — app raises `RuntimeError` on startup if the named
  Lakebase project does not exist, instead of creating one

## Motivation

When this app is embedded in a larger DAB, the bundle wants to own
the Lakebase project lifecycle (create, grant roles, enable Lakehouse
Sync, destroy). Today the app auto-creates on startup, which causes
two owners to fight over the same resource. The `USE_LAKEBASE`
opt-out already exists for "no DB at all", but there's no way to say
"use Lakebase, but don't create it".

## Changes

- `backend/lakebase.py`: new `LAKEBASE_AUTO_PROVISION` module-level
  constant; guard clause in `ensure_lakebase_project()` that raises
  with a remediation hint when project is missing and flag is off
- `README.md`: env table entry, softened 'auto-provisioned' prose,
  new 'Using with Databricks Asset Bundles' section

## Verification (manual)

| Scenario | Expected |
|---|---|
| Unset, project exists | Connects (unchanged) |
| Unset, project missing | Creates, connects (unchanged) |
| `=false`, project exists | Connects |
| `=false`, project missing | Startup fails with clear error |

Verified the unit-level guard logic with a mocked workspace client
(no network). Manual end-to-end verification against the
`cv-explorer` Lakebase project on the author's workspace before
merge.

## Backward compatibility

100%. Variable defaults to `"true"`; existing deployments unaffected.

## Test plan

- [ ] Unit verification of the guard clause (mock workspace client)
- [ ] Default-unset path still creates when project missing
- [ ] Explicit `=true` path still creates when project missing
- [ ] `=false` with existing project connects cleanly
- [ ] `=false` with missing project fails with expected message
EOF
)"
```
Expected: prints PR URL.

**Step 2: Verify PR is open**

Run:
```bash
gh pr view --repo Data-drone/db_image_labelling_app --json number,state,url,author
```
Expected: `"state":"OPEN"`, author matches the push identity.

**Step 3: Capture PR URL** — return to user.

---

## Task 9: Manual end-to-end smoke verification

This is optional-but-recommended BEFORE requesting review/merge.

**Files:** none (runtime smoke)

**Step 1: Deploy the PR branch to the existing `cv-explorer-react` app**

Run:
```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"feat/lakebase-auto-provision-switch"},"mode":"SNAPSHOT"}'
```
Expected: deployment succeeds, commit hash matches branch HEAD.

**Step 2: Verify default path still works (project exists, unset flag)**

The app doesn't currently set `LAKEBASE_AUTO_PROVISION`, so this path
is already exercised by the normal startup. Check the app is healthy:
```bash
TOKEN=$(databricks-token)
curl -sS -w "HTTP %{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  "https://cv-explorer-react-984752964297111.11.azure.databricksapps.com/api/health"
```
Expected: `{"status":"ok"}` with `HTTP 200`.

**Step 3: Verify `=false` path (optional, destructive — skip unless needed)**

Would require adding `LAKEBASE_AUTO_PROVISION=false` + a bogus
`LAKEBASE_PROJECT_ID` to `app.yaml`, redeploying, and confirming the
app fails to start with the expected error. Since Task 3 already
covers this via the mocked unit-level check, this runtime step is
optional and can be deferred to when someone actually wires up a DAB.

**Step 4: Post verification result back to the PR**

```bash
gh pr comment --repo Data-drone/db_image_labelling_app <PR#> \
  --body "Manual smoke: deployed branch to cv-explorer-react, app starts and /api/health returns 200. Guard clause verified via mocked workspace client (Task 3 step 3 of implementation plan)."
```

---

## Task 10: Revert cv-explorer-react to main

The app was deployed from the feature branch for smoke testing. Before
closing out, redeploy from main so the live app isn't pinned to an
unmerged branch.

**Step 1: Redeploy main**

Run:
```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
```
Expected: deployment succeeds.

**Step 2: Verify**

```bash
databricks apps get cv-explorer-react | grep -A2 git_source
```
Expected: `branch: main`.

---

## Rollback

If the PR causes issues post-merge:

```bash
git revert <merge-commit-hash>
git push origin main
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
```

The change is additive and guarded by a default; rollback is a normal
revert, no data migration.
