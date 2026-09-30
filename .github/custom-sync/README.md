# Direct GitHub custom-core synchronization

This control branch runs real `git cherry-pick` on GitHub Actions. It is the supported
weekly path; do not launch Work, Codex or cloud_tasks, invent commit metadata with
connector create_commit, or extract/create credentials. Only the built-in job-scoped
GITHUB_TOKEN is used.

Update `.github/custom-sync/request.json` on `maintenance/custom-sync` through the
GitHub connector, after reading both current branch heads and the last `state.json`:

```json
{
  "schema": 1,
  "request_id": "20261005-unique-id",
  "expected_head": "FULL_CURRENT_CUSTOM_DEV_SHA",
  "upstream_sha": "FULL_CURRENT_SAGERNET_TESTING_SHA"
}
```

An optional `expected_tree` SHA enforces an independently audited final tree.
Request IDs are unique alphanumeric/hyphen/underscore strings, at most 80 characters.
The initial request includes the independently audited tree. Future ordinary
upstream-only updates may omit it: all cherry-picked patches and author metadata
are still compared, and conflicts stop the run.

The fixed source patch list is the two original user commits, with their original
authors/dates/full messages. CI-only commits are listed separately in `sync.py`; subsequent CI fixes remain separate from user patches. If the user
changes custom code or CI, stop and review/update that list; never discard their
new changes. Source fingerprint excludes only the release request file. A source
change outside this controlled chain fails closed even when expected_head is known.

The job verifies current upstream SHA and expected custom head, cherry-picks and
audits, saves a backup branch, then uses an atomic `--force-with-lease`. It never
moves tags or deletes backups. `state.json` records the published source SHA/tree,
original-to-replayed mapping and backup. No source/upstream changes means no rewrite.
If the state-recording push fails after custom-dev was updated, inspect the immutable
`custom-sync-audit` artifact and branch; do not blindly retry or drop the source.

After terminal success, read state.json and verify custom-dev source SHA equals it.
Only then submit the separate custom-release/request.json on custom-dev for that
exact SHA. Observe that release run to terminal success and verify actual assets.
Then request Android signing only when the owner's genuine signing configuration
is complete. Never use inherited upstream Build/All/store-publish workflows.

Same-name stable/testing branches require their own reviewed upstream+CI guard
maintenance. This control job handles custom-dev only and never silently resets
other branches. Pause/report if a matching branch conflicts or lacks a safe path.
