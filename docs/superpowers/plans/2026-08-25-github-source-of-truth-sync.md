# GitHub Source-of-Truth Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the validated server-side backend fixes onto the latest separated backend, preserve the legacy server frontend recoverably, and make both GitHub repositories the only tracked source of truth.

**Architecture:** `MemOS_my/lwm_dev` remains an API-only backend deployed on the server. `MemOS_front/main` remains an independent React/Vite client run outside the server and communicates only through `/api/v1`; the legacy server frontend is archived rather than merged.

**Tech Stack:** Git, Python 3.11, pytest, Ruff, Docker Compose, Caddy, React 19, TypeScript 5.9, Vite 8, npm.

**Spec:** `docs/superpowers/specs/2026-08-25-github-source-of-truth-sync-design.md`

## Global Constraints

- Never stage or print `.env`, `deploy/server/.server.env`, `.memos`, database files, uploads, API keys, password hashes, tokens, or vector contents.
- Never force-push, reset a dirty worktree, delete a data volume, or run `docker compose down -v`.
- Preserve `/home/Lwm/memos-stack/MemOS` until its changes have a local backup commit.
- Keep `origin/lwm_dev` API-only: no `frontend` service and no static-page routing in backend Caddy.
- Keep `origin/main` in `MemOS_front` free of backend routes, databases, authentication secrets, and server paths.
- Server-only port and mirror values stay in ignored environment files; repository defaults remain portable.

---

### Task 1: Create Recoverable Server Snapshots

**Files:**
- Preserve: `/home/Lwm/memos-stack/MemOS` tracked modifications
- Preserve: `/home/Lwm/memos-stack/MemOS/deploy/server/docker-compose.local.yml`
- Preserve: `/home/Lwm/memos-stack/MemOS_frontend`

**Interfaces:**
- Consumes: dirty server backend rooted at commit `82d0d99c`
- Produces: local-only branch `backup/server-before-sync-20260825` and an untouched frontend directory until Task 5

- [ ] **Step 1: Verify sensitive paths are ignored**

Run:

```bash
git -C /home/Lwm/memos-stack/MemOS check-ignore -v \
  .env deploy/server/.server.env .memos/topic/topics.json
```

Expected: all three paths are reported as ignored.

- [ ] **Step 2: Create a local backup branch without changing files**

Run:

```bash
git -C /home/Lwm/memos-stack/MemOS switch -c backup/server-before-sync-20260825
git -C /home/Lwm/memos-stack/MemOS add \
  deploy/server/Caddyfile \
  deploy/server/Dockerfile.memos \
  deploy/server/docker-compose.yml \
  deploy/server/docker-compose.local.yml \
  scripts/memos_topic.py \
  src/memos/api/config.py \
  src/memos/memories/textual/relationship.py \
  src/memos/templates/memory_info_prompts.py \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py \
  tests/scripts/test_memos_topic.py
git -C /home/Lwm/memos-stack/MemOS diff --cached --check
git -C /home/Lwm/memos-stack/MemOS commit -m "chore: snapshot server fixes before repository sync"
```

Expected: one local commit; ignored secrets remain unstaged; this branch is not pushed.

- [ ] **Step 3: Confirm both worktrees are recoverable**

Run:

```bash
git -C /home/Lwm/memos-stack/MemOS status --short --branch
git -C /home/Lwm/memos-stack/MemOS-sync status --short --branch
```

Expected: backup worktree clean; sync worktree contains only committed design/plan work.

- [ ] **Step 4: Install the repository runner in the user tool directory**

The server has Python and pip but no `uv` or Poetry. Install only the runner, not a project-global
dependency:

```bash
python3 -m pip install --user uv
/home/Lwm/.local/bin/uv --version
/home/Lwm/.local/bin/uv pip install --python .venv/bin/python \
  pytest pytest-asyncio pytest-cov pytest-html ruff socksio
```

Expected: `uv` prints its version and `.venv/bin/pytest` plus `.venv/bin/ruff` exist. The project
stores test tools in Poetry-only groups that `uv` does not resolve, so later checks call these
worktree-local executables directly.

### Task 2: Merge Memory Extraction Reliability Fixes

**Files:**
- Modify: `src/memos/api/config.py`
- Modify: `src/memos/memories/textual/relationship.py`
- Modify: `src/memos/templates/memory_info_prompts.py`
- Test: `tests/api/test_llm_provider_config.py`
- Test: `tests/mem_reader/test_memory_extraction_schema.py`
- Test: `tests/memories/textual/test_relationship.py`

**Interfaces:**
- Consumes: the server snapshot changes to memory extraction
- Produces: default `MEMRADER_MAX_TOKENS=16000`, time-boundary-preserving normalization, and complete-prefix recovery for truncated normalizer JSON

- [ ] **Step 1: Apply only the three regression-test changes**

Run from `/home/Lwm/memos-stack/MemOS-sync`:

```bash
git -C /home/Lwm/memos-stack/MemOS show \
  backup/server-before-sync-20260825 -- \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py | git apply -
```

- [ ] **Step 2: Run the tests and verify the remote baseline fails**

Run:

```bash
.venv/bin/pytest \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py -q
```

Expected: failures for the 16000 default, time-range retention, and incomplete-output recovery.

- [ ] **Step 3: Apply the matching production changes**

Run:

```bash
git -C /home/Lwm/memos-stack/MemOS show \
  backup/server-before-sync-20260825 -- \
  src/memos/api/config.py \
  src/memos/memories/textual/relationship.py \
  src/memos/templates/memory_info_prompts.py | git apply -
```

The resulting `_parse_complete_event_prefix(raw: str | None) -> list[dict[str, Any]]`
must accept only complete leading objects from `{"events": [...]}` and must mark all remaining
source indexes with `incomplete_normalizer_output`.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the Step 2 command again.

Expected: all selected tests pass.

- [ ] **Step 5: Commit the extraction fixes**

```bash
git add src/memos/api/config.py \
  src/memos/memories/textual/relationship.py \
  src/memos/templates/memory_info_prompts.py \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py
git diff --cached --check
git commit -m "fix: preserve complete memories from truncated extraction"
```

### Task 3: Merge Resumable Topic Backfill

**Files:**
- Modify: `scripts/memos_topic.py`
- Test: `tests/scripts/test_memos_topic.py`

**Interfaces:**
- Consumes: `MemOSMemoryClient.get_by_ids(memory_ids: list[str])`
- Produces: `MemOSMemoryClient.list_memories(*, user_id: str, cube_id: str)`, `TopicStore.tagged_memory_ids(user_id: str, cube_id: str) -> set[str]`, and `TopicProcessor.backfill(..., ingest_batch_id: str | None) -> BackfillResult`

- [ ] **Step 1: Apply the Topic regression tests only**

```bash
git -C /home/Lwm/memos-stack/MemOS show \
  backup/server-before-sync-20260825 -- tests/scripts/test_memos_topic.py | git apply -
```

- [ ] **Step 2: Verify the tests fail on missing backfill interfaces**

```bash
.venv/bin/pytest tests/scripts/test_memos_topic.py -q
```

Expected: failures naming `list_memories`, `tagged_memory_ids`, or `backfill`.

- [ ] **Step 3: Apply the Topic implementation**

```bash
git -C /home/Lwm/memos-stack/MemOS show \
  backup/server-before-sync-20260825 -- scripts/memos_topic.py | git apply -
```

The `backfill` command must select activated `record_type=event` memories, optionally restrict
them by `ingest_batch_id`, skip every memory ID already present in the tag map (including empty
tag lists), and still recompute missing Topic summaries when the pending list is empty.

- [ ] **Step 4: Verify Topic tests and CLI parsing**

```bash
.venv/bin/pytest tests/scripts/test_memos_topic.py -q
/home/Lwm/.local/bin/uv run --frozen python scripts/memos_topic.py --help
```

Expected: tests pass and help lists `backfill` plus `--ingest-batch-id`.

- [ ] **Step 5: Commit Topic backfill**

```bash
git add scripts/memos_topic.py tests/scripts/test_memos_topic.py
git diff --cached --check
git commit -m "feat: add resumable Topic backfill"
```

### Task 4: Preserve Portable Server Deployment Fixes

**Files:**
- Modify: `tests/scripts/test_server_deployment.py`
- Modify: `deploy/server/Dockerfile.memos`
- Modify: `deploy/server/docker-compose.yml`
- Modify: `deploy/server/Caddyfile`
- Modify: `deploy/server/.server.env.example`
- Modify: `deploy/server/README_ZH.md`

**Interfaces:**
- Consumes: Compose substitution variables from `.server.env`
- Produces: `MEMOS_HTTP_PORT`, `MEMOS_HTTPS_PORT`, and `PIP_INDEX_URL` deployment inputs with portable defaults

- [ ] **Step 1: Add failing deployment assertions**

Extend `test_public_server_exposes_only_the_application_api` with behavior checks equivalent to:

```python
assert '"${MEMOS_HTTP_PORT:-80}:80"' in compose
assert '"${MEMOS_HTTPS_PORT:-443}:443"' in compose
assert "PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}" in compose
assert "default_sni {$PUBLIC_HOST}" in caddyfile
assert "frontend:" not in compose
```

Add a second test that checks `.server.env.example` and `README_ZH.md` document all three variables
without containing a real host, password, or credential.

- [ ] **Step 2: Verify the deployment tests fail**

```bash
.venv/bin/pytest tests/scripts/test_server_deployment.py -q
```

Expected: failures for missing configurable ports, build index, and default SNI.

- [ ] **Step 3: Implement portable configuration**

In `docker-compose.yml`, change the shared build block to:

```yaml
x-memos-build: &memos-build
  context: ../..
  dockerfile: deploy/server/Dockerfile.memos
  args:
    PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}
```

Change Caddy ports to:

```yaml
ports:
  - "${MEMOS_HTTP_PORT:-80}:80"
  - "${MEMOS_HTTPS_PORT:-443}:443"
```

In `Dockerfile.memos`, declare `ARG PIP_INDEX_URL=https://pypi.org/simple`, set pip timeout and
retry environment variables, and install requirements without hardcoding a regional mirror.
Add `default_sni {$PUBLIC_HOST}` to the global Caddy block while preserving the API-only handlers.
Document the variables in `.server.env.example` and `README_ZH.md`.

- [ ] **Step 4: Verify deployment behavior**

```bash
.venv/bin/pytest tests/scripts/test_server_deployment.py -q
docker compose --env-file deploy/server/.server.env.example \
  -f deploy/server/docker-compose.yml config --services
```

Expected services: `memos`, `app-backend`, `caddy`, `neo4j`, `qdrant`; no `frontend` or `companion`.

- [ ] **Step 5: Commit deployment portability**

```bash
git add deploy/server/Dockerfile.memos \
  deploy/server/docker-compose.yml \
  deploy/server/Caddyfile \
  deploy/server/.server.env.example \
  deploy/server/README_ZH.md \
  tests/scripts/test_server_deployment.py
git diff --cached --check
git commit -m "fix: preserve portable server deployment settings"
```

### Task 5: Validate and Archive the Legacy Server Frontend

**Files:**
- Read: `/home/Lwm/memos-stack/MemOS_frontend`
- Create by clone: `/home/Lwm/memos-stack/MemOS_frontend` from `noabt187/MemOS_front/main`
- Archive: `/home/Lwm/memos-stack/MemOS_frontend.server-legacy-20260825`

**Interfaces:**
- Consumes: backend `/api/v1` contract from `origin/lwm_dev`
- Produces: a clean frontend checkout whose `origin/main` equals GitHub; legacy files remain recoverable outside the active path

- [ ] **Step 1: Clone and verify the GitHub frontend in a temporary directory**

```bash
git clone --branch main --single-branch \
  https://github.com/noabt187/MemOS_front.git \
  /home/Lwm/memos-stack/MemOS_front.verify
cd /home/Lwm/memos-stack/MemOS_front.verify
npx --yes --package=node@22.14.0 -c \
  'node --version && npm ci && npm run lint && npm run build'
```

Expected: lint and build exit zero.

- [ ] **Step 2: Verify the required separated features**

Check the built source exposes `/`, `/login`, `/runtime`, `/topics`, and `/upload`; uses ordinary
anchors for navigation; and calls only `/api/v1` through `lib/api-client.ts`. Confirm there are no
server routes, Drizzle databases, password hashes, or model keys.

- [ ] **Step 3: Archive the old frontend recoverably**

After confirming `/home/Lwm/memos-stack/MemOS_frontend.server-legacy-20260825` does not exist:

```bash
mv /home/Lwm/memos-stack/MemOS_frontend \
  /home/Lwm/memos-stack/MemOS_frontend.server-legacy-20260825
mv /home/Lwm/memos-stack/MemOS_front.verify \
  /home/Lwm/memos-stack/MemOS_frontend
```

Expected: the active path is a clean `origin/main` checkout; `.env.local` remains only in the
legacy archive and is not printed or committed.

- [ ] **Step 4: Confirm no frontend push is needed**

```bash
git -C /home/Lwm/memos-stack/MemOS_frontend status --short --branch
git -C /home/Lwm/memos-stack/MemOS_frontend rev-list --left-right --count origin/main...main
```

Expected: clean status and `0 0`. If either check differs, stop before pushing and inspect.

### Task 6: Run Full Validation and Push Backend

**Files:**
- Verify: all files changed in Tasks 2–4
- Verify: `.env`, `.server.env`, `.memos`, databases, uploads, caches

**Interfaces:**
- Consumes: all prior backend commits
- Produces: a tested fast-forward update for `origin/lwm_dev`

- [ ] **Step 1: Run relevant backend tests**

```bash
.venv/bin/pytest \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py \
  tests/scripts/test_memos_topic.py \
  tests/scripts/test_memos_app_auth.py \
  tests/scripts/test_memos_frontend_api.py \
  tests/scripts/test_server_deployment.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run repository formatting and static checks**

```bash
.venv/bin/ruff check --fix \
  scripts/memos_topic.py \
  src/memos/api/config.py \
  src/memos/memories/textual/relationship.py \
  src/memos/templates/memory_info_prompts.py \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py \
  tests/scripts/test_memos_topic.py \
  tests/scripts/test_server_deployment.py
.venv/bin/ruff format \
  scripts/memos_topic.py \
  src/memos/api/config.py \
  src/memos/memories/textual/relationship.py \
  src/memos/templates/memory_info_prompts.py \
  tests/api/test_llm_provider_config.py \
  tests/mem_reader/test_memory_extraction_schema.py \
  tests/memories/textual/test_relationship.py \
  tests/scripts/test_memos_topic.py \
  tests/scripts/test_server_deployment.py
```

Expected: commands exit zero; formatting must not introduce unrelated changes. These are the
direct Ruff commands used by `make format`; Poetry is not installed on this server.

- [ ] **Step 3: Audit the outgoing commit range**

```bash
git status --short --branch
git diff --check origin/lwm_dev..HEAD
git diff --name-status origin/lwm_dev..HEAD
git log --oneline origin/lwm_dev..HEAD
```

Expected: only design, plan, approved backend code, tests, and portable deployment documentation.
No secret or runtime-data path may appear.

- [ ] **Step 4: Confirm the remote did not advance**

```bash
git fetch origin
git rev-list --left-right --count origin/lwm_dev...HEAD
```

Expected: `0 N`, where `N` is the number of local sync commits.

- [ ] **Step 5: Push by fast-forward only**

```bash
git push origin HEAD:lwm_dev
git ls-remote --heads origin lwm_dev
```

Expected: remote `lwm_dev` points to the verified local HEAD; no force option is used.

### Task 7: Converge the Server Checkout and Deploy Backend Only

**Files:**
- Update ignored: `/home/Lwm/memos-stack/MemOS/deploy/server/.server.env`
- Preserve ignored: `/home/Lwm/memos-stack/MemOS/.env`
- Preserve volumes: Neo4j, Qdrant, Topic, uploads, Caddy data/config

**Interfaces:**
- Consumes: pushed `origin/lwm_dev`
- Produces: clean server checkout and backend-only running services

- [ ] **Step 1: Return the canonical server worktree to `lwm_dev`**

```bash
git -C /home/Lwm/memos-stack/MemOS switch lwm_dev
git -C /home/Lwm/memos-stack/MemOS merge --ff-only origin/lwm_dev
git -C /home/Lwm/memos-stack/MemOS status --short --branch
```

Expected: clean `lwm_dev`, equal to `origin/lwm_dev`; ignored configs remain in place.

- [ ] **Step 2: Preserve this server's port and mirror settings**

Ensure the ignored `.server.env` contains:

```dotenv
MEMOS_HTTP_PORT=8080
MEMOS_HTTPS_PORT=443
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

Do not alter or print its existing host, email, Neo4j password, or CORS values.

- [ ] **Step 3: Render the final backend-only service list**

```bash
cd /home/Lwm/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env config --services
```

Expected: `memos`, `app-backend`, `caddy`, `neo4j`, `qdrant` only.

- [ ] **Step 4: Deploy without deleting data volumes**

```bash
sudo docker compose --env-file .server.env \
  up -d --build --wait --remove-orphans
sudo docker compose --env-file .server.env ps
```

Expected: all five backend services healthy/running; legacy `frontend` and `companion` containers
are removed as orphans; no volume is deleted.

- [ ] **Step 5: Verify the public contract**

```bash
curl --fail --silent --show-error https://$PUBLIC_HOST/api/v1/health
curl --silent --output /dev/null --write-out '%{http_code}\n' https://$PUBLIC_HOST/
```

Expected: health returns JSON success and `/` returns 404 because the server no longer hosts a
frontend page.
