# Custom releases

Request **Custom core release** (`custom-release.yml`) on `custom-dev` after upstream
synchronization and review. It never synchronizes branches itself. It builds six
pure-Go CLI archives (Linux, Windows, macOS; amd64 and arm64), tests the source,
then creates a new tag and GitHub Release. No Apple signing, package repository,
Docker registry, Android app store or upstream publishing is performed.

```
gh workflow run custom-release.yml --repo csbxd/sing-box --ref custom-dev
```

For direct GitHub connector operation, create/update
`.github/custom-release/request.json` on `custom-dev` after the source/config commit:

```json
{"schema":1,"source_sha":"FULL_40_CHARACTER_CORE_COMMIT_SHA"}
```

The SHA must already be an ancestor of the branch. The push workflow is restricted
to this single request path. It builds the immutable requested commit. Request-only
changes are excluded from source fingerprinting, so repeated requests never create
another release. Never use the same request file for arbitrary command input.
A requester may include an ignored `request_id` string to retrigger a failed build
without changing source. Do not submit until ready to create a public release.

The optional manual workflow must exist on the repository's default branch for manual dispatch.
Enable GitHub Actions for the fork if GitHub asks. Only the built-in, job-scoped
GITHUB_TOKEN is used. No personal access token or cross-repository credential is
required. Configure signed APKs separately in csbxd/sing-box-for-android.

## Version and safety policy

- Read the first semver heading in the exact source `docs/changelog.md` to identify
  its numeric X.Y.Z series. Read all published SagerNet/sing-box Releases, including
  prereleases, then choose the highest SemVer in that exact series. Stable ranks
  above prerelease; numeric prerelease components sort numerically. Do not require
  tag ancestry: upstream testing history can be rewritten. Fail if no series match.
- Stable base `v1.14.0` becomes `v1.14.0-c1`; prerelease base
  `v1.15.0-alpha.9` becomes `v1.15.0-alpha.9.c1`. Reserve a new counter above all
  existing matching tags/releases. An existing published source tree is a no-op.
- Metadata in release notes and source-metadata.json records exact source SHA,
  tree and upstream base. Never delete these markers while relying on deduplication.
- One release pipeline runs at a time. No tag is force-updated. Tags/releases are
  created only after all six builds and tests succeed. Release upload uses a draft
  first; a failed publication may leave a draft/tag. Inspect and repair it manually
  before retrying; do not delete or replace published tags automatically.
- `SING_BOX_BUILD_VERSION` lets Android embed the planned custom version without
  modifying Git tags. It accepts a version string without a leading `v`.

## Weekly direct-GitHub operation

The external weekly scheduler must use direct GitHub connectors/API and GitHub
Actions only. Do not launch Work, Codex, cloud_threads, a saved coding environment,
or another execution task. If direct access or workflow dispatch is unavailable,
stop and report the blocker rather than switching execution environments.

For same-name branches, preserve a backup of the exact current fork head, start at
current upstream SHA and replay fork-specific CI guards as separate commits.
`custom-dev` tracks upstream `testing`: replay the Go compatibility fix and service-discovery changes separately, then the CI/build configuration commit. Keep a clean
linear history, never merge upstream with a merge commit. Verify original custom
files and final tree; fail closed on overlap/conflicts. Before a history-changing
ref update, recheck the expected old SHA and stop if it changed. Never overwrite
new concurrent commits. Retain backup branches and never move existing tags.

Use only this custom workflow for release requests. Record its exact completed run
and release before requesting the Android signed release for the exact custom-core
commit. Never run inherited upstream **Build** as a substitute: it includes
unrelated publishing paths. Preserve fork guards around upstream-only publishing
jobs after each replay and inspect newly introduced publishing steps.

If build/test/dispatch fails, report it; do not create an empty release, fall back
to unsigned/debug APKs, or alter branch protection or token permissions.

Before creating a draft and again before publishing it, the workflow checks that
`custom-dev` still points to the exact triggering request commit. A user force-push,
new source update or superseding request aborts publication. Do not update the branch
while publishing; if a race leaves a draft, inspect it manually, never overwrite tags.

History synchronization must use actual `git cherry-pick`, preserving original
commit authors, author dates and full messages. Compare stable patch IDs and commit
metadata before pushing. Never reconstruct user commits with the GitHub create-commit
connector, which cannot preserve their original author metadata.
