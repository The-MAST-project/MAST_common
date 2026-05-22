---
name: mast-release
description: How to cut a coordinated release across the MAST ecosystem by stamping the same annotated git tag at HEAD of every MAST_* repo in one shot via the `mast-release` CLI. Use this skill whenever the user wants to tag a release, mark a deployment baseline, freeze a version across MAST repos, stamp a friendly name on the current snapshot, push existing release tags to origin, or audit which release tags exist where — including phrases like "cut a release", "tag this baseline", "stamp v2.0", "mark this as the operational snapshot", "release a version", "apply the same tag everywhere", "push the release tags", "what tags are in each repo", or "what's the current MAST version". Also use when the user discusses release naming conventions or wants to know how to set a friendly version name across MAST.
---

# Stamping coordinated MAST releases

This skill is the recipe for using `MAST_control/tools/mast-release` correctly. The CLI is the only sanctioned way to apply a release identity across the MAST ecosystem — tags are the single source of truth for "what version is this snapshot?", and applying them manually across ~9 repos is exactly the kind of boilerplate that creates partial-deployment states (the problem the build report was built to detect).

## When to reach for this

A "release" in MAST means: every `MAST_*` repo in the workspace gets the same annotated git tag at its current HEAD, in one coordinated operation. Use this skill when the user:

- wants to cut a release, mark a baseline, freeze a version, or stamp a name
- asks how to apply a friendly version name across MAST
- wants to push release tags to `origin` after a previous local-only tagging
- wants to audit which tags exist in which repos (drift detection)
- mentions specific tag names in a release context (e.g. "tag this as v2.0")

Do NOT reach for this skill for: per-repo tagging on a single repo, branch operations, or anything that isn't a coordinated cross-repo version stamp.

## The three operations

The CLI lives at `MAST_control/tools/mast-release` and exposes three verbs. Pick the one that matches the user's intent before doing anything else.

### 1. `tag <name> <message>` — apply a new release tag

Use when the user is creating a new release. The flow the CLI runs:

1. **Fetch every remote** in every MAST_* repo, then **preflight**. Hard-fails (block the whole batch) on: dirty working tree, detached HEAD, tag already at a different SHA than HEAD, or **the current branch being behind its canonical upstream mainline** (upstream/master → upstream/main → origin/main → origin/master, first one that resolves wins). The freshness check is the most important addition for foolproofing: tagging a stale branch silently excludes commits that have already landed on mainline, producing exactly the partial-deployment state the release process is meant to prevent.
2. Print the plan (one row per repo with branch + HEAD + action), then prompt for confirmation by re-typing the tag name. The re-type-the-name pattern is deliberate — it prevents muscle-memory `y` from misfiring a destructive coordinated change.
3. Apply `git tag -a <name> -m <message>` in every repo.
4. Run the build report. Push gate requires: single `common_sha_consistency` SHA, no per-repo errors, and every repo's new tag resolving to its current HEAD.
5. Prompt to push to `origin`. Again re-type the tag name to confirm. If declined, tags stay local and can be pushed later with `mast-release push`.

Run the command like this (note both args are positional and the message must be quoted):

```bash
# Tag each repo at the tip of eli/build-report when present, else canonical main.
MAST_control/tools/mast-release tag v2.0 "Operational baseline May 2026" \
    --from eli/build-report
```

Flags:

- `--yes` — skip both confirmation prompts (pre-tag and pre-push). Use only when the user has explicitly approved automation (CI, scripted release). For interactive use, leave it off so the human types the tag name twice.
- `--allow-stale` — skip the freshness check. Use only when the user has explicitly stated they want to tag a stale ref (rare; usually this means tagging a hotfix or operational snapshot that intentionally lags mainline). Do not reach for this flag just because the freshness check is inconvenient — the right response to a stale target is almost always to merge mainline in first.
- `--from <branch>` — in each repo, tag that local branch's tip if it exists; otherwise fall back to that repo's canonical mainline tip (upstream/master → upstream/main → origin/main → origin/master, first that resolves). Without `--from` the tagger uses current HEAD in each repo. **Strongly prefer `--from` when the release work lives on a shared working branch like `eli/build-report` that not every repo has** — it stops you from accidentally tagging an unrelated working branch in a repo where the shared branch was never created (the MAST_provisioning-tagged-on-vm-provisioning failure mode). The fallback line in the plan output makes the canonical-mainline choice explicit per repo, so the user can verify before confirming.

### 2. `push <name>` — push an already-applied tag

Use when the user previously tagged locally and now wants to publish. Re-runs the same build-report push gate before pushing. Per-repo push failures (e.g. forks with no push rights to `origin` on a given repo) are surfaced but do not abort the batch.

```bash
MAST_control/tools/mast-release push v2.0
```

### 3. `list [pattern]` — audit tags across repos

Use when the user is investigating release drift — e.g. "is v2.0 present everywhere?" or "what's the latest release?".

```bash
MAST_control/tools/mast-release list           # all tags per repo
MAST_control/tools/mast-release list 'v*'      # filter
```

A repo missing the latest release tag is a release-drift signal; flag it to the user.

## How to choose a tag name

Defer to the user. If they didn't say, offer a couple of conventions and ask:

- **Semantic version**: `v2.0`, `v2.1.3` — when the change has a clear major/minor/patch shape.
- **Date-stamped baseline**: `2026-05-operational`, `2026-Q2-baseline` — when the release is "the state of the world on this date" rather than a versioned feature set.
- **Campaign/event name**: `pre-eclipse-2026`, `commissioning-run-3` — when the release exists to support a specific operational milestone.

Tag names must be valid git refs (no spaces, no `..`, no leading `-`). The annotation message is free text and should be a single argument — quote it.

## Failure modes and what to do

### Preflight fails

The CLI exits with code `1` and prints which repo(s) failed and why. Common causes:

- **"working tree dirty"** — uncommitted changes in some repo. Ask the user whether to stash, commit, or discard. Do not silently `git stash` — the user needs to know what's about to disappear.
- **"detached HEAD"** — someone left a repo on a checked-out SHA rather than a branch. Ask which branch they intended; checkout that branch first.
- **"branch '<branch>' is stale — N commit(s) behind <ref>"** — the working branch is missing commits that already landed on the canonical mainline. This is the freshness gate. The correct response is almost always to bring the branch up to date before tagging: for each stale repo, `git checkout <branch> && git pull --ff-only <remote> <branch>` (or `git merge <canonical-ref>` if a merge commit is needed). Confirm with the user before doing this — pulling can introduce changes they haven't seen. Only fall back to `--allow-stale` if the user explicitly says they want to tag the stale state (e.g. a hotfix or operational baseline that intentionally lags mainline). After the merge/pull lands, re-run `mast-release tag` — the freshness check should now pass.
- **"tag '<name>' already exists at a different SHA"** — the requested tag already exists on at least one repo pointing somewhere else. Two options: pick a different tag name (preferred), or have the user explicitly delete the old tag with `git tag -d <name>` in every affected repo (and `git push --delete origin <name>` if it was pushed). The CLI deliberately refuses to move tags silently — re-tagging is a destructive action the user must drive.

### Tagging fails partway (exit code 2)

Some repos got tagged, some didn't. The CLI prints which. Do not auto-rollback (deleting tags is itself destructive and the partial state is informative). Show the user the list and ask how they want to proceed: delete the partial tags and retry, or fix the failing repo and re-run (already-tagged repos will show `already-tagged-noop` on the second run).

### Push gate fails (exit code 3)

Tagging succeeded but the build report shows incoherence (MAST_common SHA drift, or one repo's tag doesn't resolve to its HEAD). The push prompt is skipped. Investigate the drift — usually a submodule pointer mismatch. After fixing, the tag is still applied; re-run `mast-release push <name>` to retry the gate.

### Push fails for some repos (exit code 4)

`origin` unreachable for one or more repos (e.g. MAST_unit's `origin` remote is known-broken). The CLI lists per-repo failures and exits non-zero. The user can re-run `mast-release push <name>` after fixing the remote, or push manually for the failing repos.

## Surface the resulting friendly name

After a successful tag, the build report's `head_describe` field automatically picks up the new tag name on every repo. This is the "friendly name" surfacing for free — there is no separate manifest file to update, no second source of truth to keep in sync. If the user asks "what's the current MAST version?", point them at `head_describe` from `GET /build-report` on any service, or `python build_report.py` at the workspace root.

## Don'ts

- Do not invent a "release manifest" file (`releases/*.toml` or similar). The user explicitly rejected this — multiple sources of truth.
- Do not push without the explicit confirmation prompt landing. Local-then-explicit-push is the contract.
- Do not skip MAST_provisioning or any other repo just because it's on a different branch (`eli/vm-provisioning`). The contract is "tag every MAST_* repo regardless".
- Do not re-implement the coordination logic — always shell out to `mast-release`.
