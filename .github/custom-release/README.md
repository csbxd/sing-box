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

- Read all published SagerNet/sing-box Releases, including prereleases. Fetch tags
  into a separate namespace, test ancestry, and choose the smallest commit-graph
  distance to this exact source SHA (lexicographic tag tie-break).
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

For same-name branches, compare exact SHAs, preserve custom commits, and push only
non-force fast-forward or verified merge commits. `custom-dev` tracks upstream
`testing`. Divergence/conflicts require content review, not a reset/rebase or blind
merge. Recheck branch tips before pushing. Run this release workflow only when
source changes. Record its exact completed run and release before dispatching the
Android signed release for that exact custom-core commit. Never run the inherited
upstream **Build** workflow as a substitute: it includes unrelated publishing paths.

If build/test/dispatch fails, report it; do not create an empty release, fall back
to unsigned/debug APKs, or alter branch protection or token permissions.
