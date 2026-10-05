"""Real-Git transaction safety tests; run by the repository Actions test job.

Every origin, upstream, checkout, hook, and race is isolated in a temporary
directory. GIT_ALLOW_PROTOCOL=file prevents accidental network access. The only
test doubles are observation/race-injection wrappers around real sync functions;
cherry-picks, metadata/patch audits, leases, hooks, and atomic pushes run Git.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sync


class TransactionTests(unittest.TestCase):
    CONTROL = "maintenance/custom-sync"
    BRANCHES = ("stable", "custom-dev")
    REQUEST_ID = "transaction-fixture"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="custom-sync-transaction-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.upstream = self.root / "upstream.git"
        self.work = self.root / "work"
        self.runner = self.root / "runner"
        self.runner.mkdir()
        home = self.root / "home"
        home.mkdir()
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_COMMITTER_DATE": "2002-03-04T05:06:07+00:00",
            "GITHUB_REPOSITORY": "csbxd/sing-box",
            "GITHUB_REF": "refs/heads/" + self.CONTROL,
            "RUNNER_TEMP": str(self.runner),
        }
        env_patch = patch.dict(os.environ, environment, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.raw_git("init", "--bare", "--quiet", self.origin, cwd=self.root)
        self.raw_git("init", "--bare", "--quiet", self.upstream, cwd=self.root)
        self.raw_git("init", "--quiet", self.seed, cwd=self.root)
        self.raw_git("config", "user.name", "Fixture Maintainer")
        self.raw_git("config", "user.email", "fixture@example.invalid")
        self.raw_git("config", "commit.gpgsign", "false")
        self.raw_git("config", "rerere.enabled", "false")
        self.write("base.txt", b"base\n")
        self.write("shared.txt", b"original shared line\n")
        self.base = self.commit("Fixture base\n")
        self.sources = {}
        self.originals = {}
        self.upstream_heads = {}
        self.pushes = []
        self.git_calls = []

    def raw_git(self, *args, cwd=None, data=None, env=None):
        effective_env = dict(os.environ)
        effective_env.update(env or {})
        return subprocess.check_output(
            ["git", *map(str, args)],
            cwd=cwd or self.seed,
            input=data,
            env=effective_env,
            stderr=subprocess.PIPE,
        )

    def g(self, *args, cwd=None, data=None, env=None):
        return self.raw_git(*args, cwd=cwd, data=data, env=env).decode().strip()

    def write(self, path, data):
        destination = self.seed / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def commit(self, message, author=None):
        self.raw_git("add", "--all")
        author_env = {
            "GIT_AUTHOR_NAME": author or "Fixture Author",
            "GIT_AUTHOR_EMAIL": "original-author@example.invalid",
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:06-03:30",
        }
        self.raw_git(
            "commit", "--quiet", "--cleanup=verbatim", "--file=-",
            data=message.encode("utf-8"), env=author_env,
        )
        return self.g("rev-parse", "HEAD")

    def tree(self, sha, cwd=None):
        return self.g("rev-parse", sha + "^{tree}", cwd=cwd)

    def tree_fingerprint(self, sha, cwd=None):
        entries = self.raw_git("ls-tree", "-r", "-z", sha, cwd=cwd).split(b"\0")
        entries = [
            entry for entry in entries
            if entry and entry.split(b"\t", 1)[1]
            != b".github/custom-release/request.json"
        ]
        return hashlib.sha256(b"\0".join(entries)).hexdigest()

    def raw_metadata(self, sha, cwd=None):
        commit = self.raw_git("cat-file", "commit", sha, cwd=cwd)
        headers, separator, message = commit.partition(b"\n\n")
        self.assertEqual(separator, b"\n\n")
        author = next(line for line in headers.splitlines()
                      if line.startswith(b"author "))
        return author, message

    def stable_patch_id(self, sha, cwd=None):
        diff = self.raw_git(
            "show", "--binary", "--format=", "--no-ext-diff", "--no-textconv",
            sha, cwd=cwd,
        )
        return self.raw_git("patch-id", "--stable", cwd=cwd, data=diff).split()[0]

    def fixture(self, advanced=None, conflict=None):
        if advanced is None:
            advanced = set(self.BRANCHES)
        advanced = set(advanced)
        config = {"schema": 1, "branches": {}}
        state = {"schema": 2, "branches": {}}
        targets = []
        for branch in self.BRANCHES:
            self.raw_git("checkout", "--quiet", "--detach", self.base)
            self.write("shared.txt", ("custom shared line: " + branch + "\n").encode())
            self.write(branch + ".txt", ("custom text: " + branch + "\n").encode())
            first = self.commit(
                "Replay text for " + branch + "\n\n"
                "Full message line one.\nFull message line two.\n\n"
                "Reviewed-by: Fixture Reviewer <reviewer@example.invalid>\n\n",
                author="Zoë Original " + branch,
            )
            self.write(branch + ".bin", b"\0\xff\x01binary payload:" + branch.encode())
            second = self.commit(
                "Replay binary for " + branch + "\n\n"
                "Preserve this paragraph and its trailing blank lines.\n\n\n",
                author="Binary Original " + branch,
            )
            self.sources[branch] = second
            self.originals[branch] = [first, second]
            self.raw_git("push", "--quiet", self.origin,
                         second + ":refs/heads/" + branch)
            upstream_branch = "testing" if branch == "custom-dev" else branch
            upstream = self.base
            if branch in advanced:
                self.raw_git("checkout", "--quiet", "--detach", self.base)
                self.write("upstream-" + branch + ".txt", b"new upstream content\n")
                if branch == conflict:
                    self.write("shared.txt", b"incompatible upstream shared line\n")
                upstream = self.commit("Advance upstream for " + branch + "\n")
            self.upstream_heads[branch] = upstream
            self.raw_git("push", "--quiet", self.upstream,
                         upstream + ":refs/heads/" + upstream_branch)
            config["branches"][branch] = {
                "upstream_branch": upstream_branch,
                "replay_commits": self.originals[branch],
            }
            state["branches"][branch] = {
                "upstream_sha": self.base,
                "source_sha": second,
                "source_tree": self.tree(second),
                "source_fingerprint": self.tree_fingerprint(second),
                "replay_commits": self.originals[branch],
            }
            targets.append({
                "branch": branch,
                "expected_head": second,
                "upstream_sha": upstream,
            })
        self.request = {
            "schema": 2, "request_id": self.REQUEST_ID, "targets": targets,
        }
        self.raw_git("checkout", "--quiet", "--detach", self.base)
        for name, value in (
            ("branches.json", config), ("state.json", state),
            ("request.json", self.request),
        ):
            self.write(".github/custom-sync/" + name,
                       (json.dumps(value, indent=2) + "\n").encode())
        self.control = self.commit("Reviewed synchronization fixture\n")
        self.raw_git("push", "--quiet", self.origin,
                     self.control + ":refs/heads/" + self.CONTROL)
        self.raw_git("--git-dir", self.origin, "symbolic-ref", "HEAD",
                     "refs/heads/" + self.CONTROL)
        self.raw_git("clone", "--quiet", "--branch", self.CONTROL,
                     self.origin, self.work, cwd=self.root)
        self.raw_git("config", "user.name", "Fixture Runner", cwd=self.work)
        self.raw_git("config", "user.email", "runner@example.invalid", cwd=self.work)
        self.raw_git("config", "url." + self.upstream.as_uri() + ".insteadOf",
                     "https://github.com/SagerNet/sing-box.git", cwd=self.work)
        os.environ["GITHUB_SHA"] = self.control
        self.initial_refs = self.refs()
        self.initial_state = self.remote_state_bytes()
        self.initial_state_file = (
            self.work / ".github/custom-sync/state.json"
        ).read_bytes()

    def refs(self):
        rows = self.g("--git-dir", self.origin, "for-each-ref",
                      "--format=%(refname) %(objectname)").splitlines()
        return dict(row.split(" ", 1) for row in rows)

    def remote_state_bytes(self):
        return self.raw_git(
            "--git-dir", self.origin, "show",
            "refs/heads/" + self.CONTROL + ":.github/custom-sync/state.json",
        )

    def remote_commit(self, repository, parent, message):
        tree = self.g("--git-dir", repository, "rev-parse", parent + "^{tree}")
        return self.g("--git-dir", repository, "commit-tree", tree, "-p", parent,
                      data=(message + "\n").encode(), env={
                          "GIT_AUTHOR_NAME": "Concurrent Fixture Writer",
                          "GIT_AUTHOR_EMAIL": "concurrent@example.invalid",
                          "GIT_AUTHOR_DATE": "2003-04-05T06:07:08+05:45",
                          "GIT_COMMITTER_NAME": "Concurrent Fixture Writer",
                          "GIT_COMMITTER_EMAIL": "concurrent@example.invalid",
                      })

    def move(self, repository, branch, sha):
        self.raw_git("--git-dir", repository, "update-ref",
                     "refs/heads/" + branch, sha)

    def backup(self, branch):
        return "backup/sync-" + self.REQUEST_ID + "/" + branch

    def save_request(self):
        path = self.work / ".github/custom-sync/request.json"
        path.write_text(json.dumps(self.request, indent=2) + "\n")
        self.raw_git("add", str(path), cwd=self.work)
        self.raw_git("commit", "--quiet", "-m", "Adjust reviewed test request",
                     cwd=self.work)
        self.control = self.g("rev-parse", "HEAD", cwd=self.work)
        self.raw_git("push", "--quiet", "origin",
                     "HEAD:refs/heads/" + self.CONTROL, cwd=self.work)
        os.environ["GITHUB_SHA"] = self.control
        self.initial_refs = self.refs()

    def invoke(self, *, failure=None, before_push=None, after_history=None):
        real_git = sync.git
        real_history = sync.verify_known_history

        def observed_git(*args, cwd=None):
            self.git_calls.append(args)
            if args and args[0] == "push":
                self.pushes.append(args)
                if before_push is not None and len(self.pushes) == 1:
                    before_push()
            return real_git(*args, cwd=cwd)

        def observed_history(*args, **kwargs):
            result = real_history(*args, **kwargs)
            if after_history is not None:
                after_history(args[0])
            return result

        previous_cwd = Path.cwd()
        try:
            os.chdir(self.work)
            with patch.object(sync, "git", side_effect=observed_git), \
                    patch.object(sync, "verify_known_history",
                                 side_effect=observed_history):
                if failure is None:
                    sync.main()
                else:
                    with self.assertRaisesRegex(
                        (RuntimeError, subprocess.CalledProcessError), failure,
                    ):
                        sync.main()
        finally:
            os.chdir(previous_cwd)

    def audit(self):
        return json.loads((self.work / "sync-result.json").read_text())

    def assert_no_remote_changes(self, expected=None):
        self.assertEqual(self.refs(), expected or self.initial_refs)
        if expected is None or expected.get("refs/heads/" + self.CONTROL) == self.control:
            self.assertEqual(self.remote_state_bytes(), self.initial_state)

    def assert_atomic_push(self, changed):
        self.assertEqual(len(self.pushes), 1, self.pushes)
        args = self.pushes[0]
        self.assertIn("--atomic", args)
        self.assertIn(
            "--force-with-lease=refs/heads/" + self.CONTROL + ":" + self.control,
            args,
        )
        destinations = {
            arg.split(":", 1)[1] for arg in args
            if ":" in arg and not arg.startswith("-")
        }
        expected = {"refs/heads/" + self.CONTROL}
        for branch in changed:
            expected.update({
                "refs/heads/" + branch,
                "refs/heads/" + self.backup(branch),
            })
            self.assertIn(
                "--force-with-lease=refs/heads/" + branch + ":" + self.sources[branch],
                args,
            )
            self.assertIn(
                "--force-with-lease=refs/heads/" + self.backup(branch) + ":",
                args,
            )
        self.assertEqual(destinations, expected)

    def test_success_replays_text_and_binary_with_full_metadata_in_one_transaction(self):
        self.fixture()
        self.invoke()
        self.assert_atomic_push(self.BRANCHES)
        refs = self.refs()
        state = json.loads(self.remote_state_bytes())
        audit = self.audit()
        self.assertEqual(set(audit["applied"]), set(self.BRANCHES))
        self.assertEqual({r["branch"] for r in audit["targets"]}, set(self.BRANCHES))
        self.assertEqual(audit["status"], "verified")
        control = refs["refs/heads/" + self.CONTROL]
        self.assertEqual(self.g("rev-parse", control + "^", cwd=self.work), self.control)
        self.assertEqual(
            self.g("diff-tree", "--no-commit-id", "--name-only", "-r",
                   control, cwd=self.work),
            ".github/custom-sync/state.json",
        )
        for branch in self.BRANCHES:
            record = state["branches"][branch]
            head = refs["refs/heads/" + branch]
            self.assertNotEqual(head, self.sources[branch])
            self.assertEqual(record["source_sha"], head)
            self.assertEqual(record["source_tree"], self.tree(head, cwd=self.work))
            self.assertEqual(record["source_fingerprint"],
                             self.tree_fingerprint(head, cwd=self.work))
            self.assertEqual(refs["refs/heads/" + self.backup(branch)],
                             self.sources[branch])
            replayed = self.g(
                "rev-list", "--reverse", self.upstream_heads[branch] + ".." + head,
                cwd=self.work,
            ).splitlines()
            self.assertEqual(len(replayed), 2)
            self.assertEqual(self.g("rev-parse", replayed[0] + "^", cwd=self.work),
                             self.upstream_heads[branch])
            self.assertEqual([r["cherry_pick"] for r in record["mapping"]], replayed)
            for original, new in zip(self.originals[branch], replayed):
                self.assertNotEqual(original, new)
                self.assertEqual(self.raw_metadata(original, cwd=self.work),
                                 self.raw_metadata(new, cwd=self.work))
                self.assertEqual(self.stable_patch_id(original, cwd=self.work),
                                 self.stable_patch_id(new, cwd=self.work))
                self.assertEqual(
                    sync.patch_id(new, self.work).encode(),
                    self.stable_patch_id(new, cwd=self.work),
                )
        cherry_picks = [args for args in self.git_calls if "cherry-pick" in args]
        self.assertEqual(len(cherry_picks), 4)
        for args in cherry_picks:
            self.assertIn("rerere.enabled=false", args)
            self.assertIn("--cleanup=verbatim", args)

    def test_mixed_transaction_does_not_write_unchanged_target_or_backup(self):
        self.fixture(advanced={"custom-dev"})
        self.invoke()
        self.assert_atomic_push({"custom-dev"})
        refs = self.refs()
        self.assertEqual(refs["refs/heads/stable"], self.sources["stable"])
        self.assertNotIn("refs/heads/" + self.backup("stable"), refs)
        self.assertEqual(self.audit()["applied"], ["custom-dev"])

    def test_conflict_in_later_target_leaves_every_remote_ref_unchanged(self):
        self.fixture(conflict="custom-dev")
        self.invoke(failure="(?i)(cherry-pick|conflict|returned non-zero)")
        self.assertTrue(any("cherry-pick" in args for args in self.git_calls))
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_changed_expected_tree_rejected_before_any_push(self):
        self.fixture()
        self.request["targets"][-1]["expected_tree"] = self.tree(self.base)
        self.save_request()
        self.invoke(failure="(?i)tree")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_source_change_during_audit_is_not_overwritten(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.sources["stable"], "Concurrent source")
        expected = dict(self.initial_refs, **{"refs/heads/stable": rival})
        fired = []

        def race(branch):
            if branch == "stable" and not fired:
                fired.append(True)
                self.move(self.origin, "stable", rival)

        self.invoke(failure="(?i)(changed|moved|stale|lease)", after_history=race)
        self.assertTrue(fired)
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes(expected)

    def test_stale_control_at_start_is_rejected(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.control, "Concurrent control")
        self.move(self.origin, self.CONTROL, rival)
        expected = self.refs()
        self.invoke(failure="(?i)(control|changed|stale|checkout)")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes(expected)

    def test_github_sha_must_match_the_checked_out_control_commit(self):
        self.fixture()
        os.environ["GITHUB_SHA"] = self.base
        self.invoke(failure="(?i)(sha|control|checkout|head)")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_source_lease_race_at_push_rejects_all_refs(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.sources["custom-dev"], "Source push race")
        expected = dict(self.initial_refs, **{"refs/heads/custom-dev": rival})
        self.invoke(
            failure="(?i)(push|lease|returned non-zero)",
            before_push=lambda: self.move(self.origin, "custom-dev", rival),
        )
        self.assert_atomic_push(self.BRANCHES)
        self.assert_no_remote_changes(expected)
        self.assertEqual(self.audit()["applied"], [])
        self.assertEqual(self.audit()["status"], "push_failed_inspect_remote")

    def test_control_lease_race_at_push_rejects_sources_and_backups(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.control, "Control push race")
        expected = dict(self.initial_refs, **{"refs/heads/" + self.CONTROL: rival})
        self.invoke(
            failure="(?i)(push|lease|returned non-zero)",
            before_push=lambda: self.move(self.origin, self.CONTROL, rival),
        )
        self.assert_atomic_push(self.BRANCHES)
        self.assert_no_remote_changes(expected)
        self.assertEqual(self.remote_state_bytes(), self.initial_state)
        self.assertEqual(self.audit()["applied"], [])

    def test_existing_fast_forward_related_backup_is_never_replaced(self):
        self.fixture()
        self.move(self.origin, self.backup("custom-dev"), self.base)
        expected = self.refs()
        self.invoke(failure="(?i)backup")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes(expected)

    def test_backup_creation_race_requires_absence_even_when_update_would_fast_forward(self):
        self.fixture()
        branch = self.backup("custom-dev")
        expected = dict(self.initial_refs, **{"refs/heads/" + branch: self.base})
        self.invoke(
            failure="(?i)(push|lease|returned non-zero)",
            before_push=lambda: self.move(self.origin, branch, self.base),
        )
        self.assert_atomic_push(self.BRANCHES)
        self.assert_no_remote_changes(expected)
        self.assertEqual(self.audit()["applied"], [])

    def reject_ref_with_hook(self, ref):
        marker = self.root / "hook-ran"
        hook = self.origin / "hooks/update"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$1\" >> '" + str(marker) + "'\n"
            "if [ \"$1\" = '" + ref + "' ]; then\n"
            "  echo 'intentional transaction test rejection' >&2\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        )
        hook.chmod(0o755)
        self.invoke(failure="(?i)(push|returned non-zero)")
        self.assert_atomic_push(self.BRANCHES)
        self.assertIn(ref, marker.read_text().splitlines())
        self.assert_no_remote_changes()
        self.assertEqual(self.audit()["applied"], [])

    def test_receive_hook_rejecting_one_source_prevents_partial_publication(self):
        self.fixture()
        self.reject_ref_with_hook("refs/heads/custom-dev")

    def test_receive_hook_rejecting_state_prevents_source_and_backup_publication(self):
        self.fixture()
        self.reject_ref_with_hook("refs/heads/" + self.CONTROL)

    def test_noop_validates_trees_without_source_state_or_backup_writes(self):
        self.fixture(advanced=set())
        for target in self.request["targets"]:
            target["expected_tree"] = self.tree(target["expected_head"])
        self.save_request()
        self.invoke()
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()
        self.assertEqual(self.g("rev-parse", "HEAD", cwd=self.work), self.control)
        self.assertEqual(
            (self.work / ".github/custom-sync/state.json").read_bytes(),
            self.initial_state_file,
        )
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))
        self.assertFalse(any(args and args[0] == "commit" for args in self.git_calls))
        self.assertEqual(self.audit()["applied"], [])
        self.assertTrue(all(record["skip"] for record in self.audit()["targets"]))
        self.assertEqual(self.audit()["status"], "noop")

    def test_noop_still_rejects_an_incorrect_expected_tree(self):
        self.fixture(advanced=set())
        self.request["targets"][-1]["expected_tree"] = self.tree(self.base)
        self.save_request()
        self.invoke(failure="(?i)tree")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def noop_race(self, kind):
        self.fixture(advanced=set())
        repository = self.upstream if kind == "upstream" else self.origin
        branch = self.CONTROL if kind == "control" else "stable"
        parent = self.control if kind == "control" else (
            self.base if kind == "upstream" else self.sources["stable"]
        )
        rival = self.remote_commit(repository, parent, "No-op " + kind + " race")
        expected = dict(self.initial_refs)
        if kind != "upstream":
            expected["refs/heads/" + branch] = rival
        fired = []

        def race(audited_branch):
            if audited_branch == "stable" and not fired:
                fired.append(True)
                self.move(repository, branch, rival)

        self.invoke(failure="(?i)(changed|moved|stale|control|upstream|lease)",
                    after_history=race)
        self.assertTrue(fired)
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes(expected)
        self.assertEqual(self.g("rev-parse", "HEAD", cwd=self.work), self.control)
        self.assertEqual(
            (self.work / ".github/custom-sync/state.json").read_bytes(),
            self.initial_state_file,
        )

    def test_noop_rechecks_live_source_after_audit(self):
        self.noop_race("source")

    def test_noop_rechecks_live_control_after_audit(self):
        self.noop_race("control")

    def test_noop_rechecks_live_upstream_after_audit(self):
        self.noop_race("upstream")

    def test_changed_transaction_rechecks_upstream_after_audit(self):
        self.fixture()
        rival = self.remote_commit(self.upstream, self.upstream_heads["stable"],
                                   "Concurrent upstream")
        fired = []

        def race(branch):
            if branch == "stable" and not fired:
                fired.append(True)
                self.move(self.upstream, "stable", rival)

        self.invoke(failure="(?i)(upstream|moved|changed|stale)", after_history=race)
        self.assertTrue(fired)
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_unrecorded_stable_commit_is_rejected_even_with_identical_tree(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.sources["stable"], "Unreviewed empty commit")
        self.move(self.origin, "stable", rival)
        self.request["targets"][0]["expected_head"] = rival
        self.save_request()
        self.invoke(failure="(?i)(unrecorded|review|outside|history)")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_unrecorded_custom_code_change_is_rejected(self):
        self.fixture()
        self.raw_git("checkout", "--quiet", "--detach", self.sources["custom-dev"])
        self.write("unreviewed.txt", b"unreviewed custom change\n")
        rival = self.commit("Unreviewed custom code\n")
        self.raw_git("push", "--quiet", self.origin,
                     rival + ":refs/heads/custom-dev")
        self.request["targets"][1]["expected_head"] = rival
        self.save_request()
        self.invoke(failure="(?i)(changed|review|outside|fingerprint)")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()

    def test_unrecorded_empty_custom_commit_is_not_a_release_request(self):
        self.fixture()
        rival = self.remote_commit(self.origin, self.sources["custom-dev"],
                                   "Unreviewed empty custom commit")
        self.move(self.origin, "custom-dev", rival)
        self.request["targets"][1]["expected_head"] = rival
        self.save_request()
        self.invoke(failure="(?i)(release-request|review|descendant|only)")
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()


if __name__ == "__main__":
    unittest.main()
