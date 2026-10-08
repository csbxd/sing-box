# Direct GitHub branch synchronization

This control branch runs real `git cherry-pick` on GitHub Actions. Read-only jobs
use the job-scoped GITHUB_TOKEN with contents:read. The final atomic push uses the
owner-managed repository Actions secret CUSTOM_SYNC_TOKEN. Do not launch Work/Codex/cloud tasks,
extract credentials, or reconstruct user commit metadata with connector create_commit.

## Scope and reviewed state

`branches.json` explicitly whitelists all **41 existing same-name upstream branches**
plus `custom-dev → testing`. `state.json` records their accepted source histories and
fingerprints. Stable/testing retain their ordered two CI-only guard commits.
Custom-dev retains the two original user commits plus separate CI-only commits.
Other 39 branches have no fork-specific commits and synchronize to exact upstream.
Unknown/new branch intersections require policy review before adding to this list;
never silently drop a user's commits. Do not modify `state.json` merely to silence a
source/history mismatch. Only independently audited source/CI updates may change it.

## Weekly connector request

Read current branches in both repositories and compare every existing same-name
intersection against the whitelist. Read reviewed replay policies/state. For every
changed target (or all targets for a no-op check), create/update
`.github/custom-sync/request.json` on **maintenance/custom-sync**:

```json
{
  "schema": 2,
  "request_id": "20261005-unique-id",
  "targets": [
    {"branch":"custom-dev","expected_head":"FULL_CURRENT_FORK_SHA","upstream_sha":"FULL_SAGERNET_TESTING_SHA"},
    {"branch":"testing","expected_head":"FULL_CURRENT_FORK_SHA","upstream_sha":"FULL_SAGERNET_TESTING_SHA"},
    {"branch":"stable","expected_head":"FULL_CURRENT_FORK_SHA","upstream_sha":"FULL_SAGERNET_STABLE_SHA"}
  ]
}
```

Use actual lowercase40-character SHAs. request_id is1–80 alphanumeric/hyphen/underscore
characters, unique for a changing synchronization. An optional per-target
`expected_tree` enforces an independently audited final tree. Legacy schema1 for
custom-dev remains supported. Request files must never contain credentials.

The job preflights **all requested targets before writing any source branch**:
- Require each exact live fork/upstream head and reviewed replay whitelist
- Require recorded source ancestry and fingerprint; reject unrecorded user commits
- Only custom-dev release-request-only descendant commits may be ignored
- For changed upstream, start at its exact commit and run actual ordered cherry-picks
- Preserve original author/email/date/full message and compare stable patch IDs
- Abort on conflicts, changed patches, unexpected metadata or dirty worktrees
- Prepare backup refs, source updates and state locally, then publish all of them in
  **one** `git push --atomic` transaction, with exact source/control head leases and
  create-only backup leases; never move release tags

No source/upstream/replay-list change means no rewrite. For upstream-only branches,
changed history is safely updated to the exact upstream commit only if the saved
fork history is still unchanged. CUSTOM_SYNC_TOKEN pushes can trigger downstream
workflows. Inspect every candidate's workflow/publishing changes before a live
request, retaining the reviewed stable/testing/custom-dev guards. Authenticated
updates to oldstable or unstable currently fail closed because their inherited
Build triggers still match those branches; separately review CI guards before
changing them. No-op checks for those branches remain allowed. Never substitute
unreviewed direct connector source-ref updates.

All requested targets are audited before publication. A rejected source, control,
backup lease or server hook rejects the entire transaction: no partial source,
backup or state updates. The workflow commit must still be the exact live control
head before preflight and immediately before publication. Backups are create-only
and remain retained after success. All-no-op requests perform no remote writes.

Read `sync-result.json` from the `custom-sync-audit` artifact and `state.json` after
the run. The audit records the planned state commit and a terminal verified/noop
status. A transport error can leave the client uncertain even for an atomic push;
inspect all exact remote refs and the planned state commit before any fresh request.
Never retry with non-atomic or unleased pushes. Never update state to conceal a mismatch.

The read-only **Validate atomic core sync** workflow runs the existing request/history
unit tests and temporary-bare-repository integration tests on control-code changes.
These tests exercise real cherry-picks, metadata and patch preservation, conflicts,
no-op behavior, stale source/control heads, backup races and server-side rejection.
Require successful tests and independent review of the exact repair commit before
submitting a real synchronization request.

## Release sequence

After terminal sync success, verify each changed source head against state. If
custom-dev's source fingerprint is unchanged and already released, create no release
request. Otherwise create/update `.github/custom-release/request.json` on custom-dev
with schema1 and the exact state source_sha. Observe its terminal success and assets.
Never dispatch inherited Build/All/store-publish workflows as a substitute.

Android has its own repository-scoped synchronization/signing workflows and token.
After Android sync and real owner signing setup, request its signed release using
exact Android source SHA and the verified custom-core release SHA. Never generate
or transmit signing keys, publish unsigned/debug fallbacks, or use cross-repo tokens.

## Explicitly reviewed lifecycle compatibility

The owner approved resolving the service-discovery conflict on 2026-10-05.
The original feature commit remains a real cherry-pick with its raw author/date
and complete message preserved. Its only exception is the two obsolete lifecycle
integration hunks in `box.go`, resolved to a fully reviewed, hash-pinned file for
exact branch `custom-dev`, original `8eedb38da3d5a7ab6c06356210c93301ed55d733`,
and upstream `fe92ab3e78a9bb7d448c155ef6906218e2ca5453`.
This exact new SHA was independently re-reviewed on 2026-10-08: its upstream
`box.go` blob is identical to the earlier 2026-10-05 candidate. The exception remains
limited to the pinned resolved file; all other patches must retain equivalence.
The resolution is not reusable for another upstream or commit. All other feature
files retain binary-inclusive stable patch equivalence. The audit explicitly
distinguishes `reviewed-resolution` from `exact` rather than claiming full patch
equivalence for the resolved feature commit.

A separate compatibility replay commit adapts the SD manager and live-test setup,
adds lifecycle regression tests, and leaves the original discovery/cache/dialer
logic and existing test assertions intact. Auxiliary maintenance commits are
review material, not release targets.

Before publication, submit a schema2 request at
`.github/custom-sync/validation-request.json`. The read-only candidate workflow
runs all safety tests, actual cherry-picks with `--prepare-only`, full Go tests,
and SD/dialer/lifecycle race tests. It cannot push refs. Independently review its
audit and require each changed target's exact `expected_tree` in the subsequent
live request. No failed validation authorizes skipping checks or changing state.

## Authorized local execution on 2026-10-08

The owner explicitly authorized this asa local-task synchronization and release.
The same audited transaction and exact leases apply. SSH owner pushes trigger
workflows, so replay the separate inherited Build trigger guard on stable/testing
and custom-dev. Test/Lint remain enabled. Missing release requests safely skip;
existing invalid requests fail. This one-time authorization does not change the
weekly connector-only policy.

## Owner-managed synchronization secret

The owner saves CUSTOM_SYNC_TOKEN in each repository's Actions secrets. The
connector never reads or writes its value. It needs permission to push this
repository, including workflow files; no credentials are generated by this route.

Both checkouts disable persisted credentials. Unit tests and preparation run
without the PAT in child-process environments. The trusted control script removes
the secret from its environment immediately and fails closed when the required
secret is empty. Only the final atomic Git push receives it through an ephemeral,
exact-destination askpass helper, never through a URL, command argument, request,
Git config, audit artifact or persisted credential file. The helper is deleted
after the push. Existing exact leases and all source/metadata/patch/tree checks
remain mandatory. Only audited control code runs in the credentialed step; never
build or execute candidate source there.

A fresh all-target no-op request verifies secret presence and synchronization
guards without changing source/state/backups or publishing a release. It does
not exercise authenticated push permissions, prove token scopes/expiry, or prove
revocation of any previously exposed token. Missing or invalid credentials must
never cause fallback to GITHUB_TOKEN or removal of atomic/lease safeguards.

## Isolated real push permission proof

The one-shot `push_probe.py` is separate from synchronization. After reviewing a
CI-only code commit and its successful tests, create only
`.github/custom-sync/push-probe-request.json` in a direct child commit containing
`{"schema":1,"request_id":"UNIQUE-LOWERCASE-ID","code_commit":"EXACT_REVIEWED_SHA"}`.
Use 6–60 lowercase letters/digits/hyphens for the actual request ID.

The dedicated workflow first runs tests without the owner secret. Its trusted
probe step removes CUSTOM_SYNC_TOKEN from the process environment immediately,
then reuses sync.py's exact atomic push credential helper. It never calls sync
or changes its request/state/replay policies. First it atomically creates two
unique `permission-probe/ID/content` and `permission-probe/ID/workflow` branches
with only an inert PROBE.md and create-only leases. Next it atomically updates
both with exact expected-head leases, adding a workflow_dispatch-only workflow
whose sole job is disabled and permissions are empty. The fixture has no
push/PR/schedule trigger, external actions, secrets or release behavior.

All existing branch/tag refs, including source/control/backups, must be identical
before and after the two transactions. The probe branches are retained as audit
evidence; no ref deletion occurs. Repeated attempts or existing probe refs fail
closed. On failure inspect the exact remote refs and staged audit before any
fresh request. No credential fallback, unleased force, or permission expansion
is allowed. The proof establishes real Contents and workflow-file push rights
for these isolated refs, not exemption from source-branch-specific rules, token
expiry in the future, or revocation of a previously exposed credential.
