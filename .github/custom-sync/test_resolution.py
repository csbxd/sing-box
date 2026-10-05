"""Real-Git tests for exact, reviewed conflict resolutions and read-only audits.

The shared transaction fixture restricts Git to temporary file:// repositories.
Only observation and deliberate tamper/race injection are mocked; cherry-picks,
continuations, metadata comparisons, binary patch IDs, and blob checks run Git.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import sync
import test_transaction as transaction


class ResolutionTests(unittest.TestCase):
    # Reuse fixture helpers without importing or inheriting its TestCase class,
    # which would make unittest discover the existing transaction tests twice.
    CONTROL = transaction.TransactionTests.CONTROL
    BRANCHES = transaction.TransactionTests.BRANCHES
    REQUEST_ID = transaction.TransactionTests.REQUEST_ID
    setUp = transaction.TransactionTests.setUp
    raw_git = transaction.TransactionTests.raw_git
    g = transaction.TransactionTests.g
    write = transaction.TransactionTests.write
    commit = transaction.TransactionTests.commit
    tree = transaction.TransactionTests.tree
    tree_fingerprint = transaction.TransactionTests.tree_fingerprint
    raw_metadata = transaction.TransactionTests.raw_metadata
    stable_patch_id = transaction.TransactionTests.stable_patch_id
    fixture = transaction.TransactionTests.fixture
    refs = transaction.TransactionTests.refs
    remote_state_bytes = transaction.TransactionTests.remote_state_bytes
    remote_commit = transaction.TransactionTests.remote_commit
    move = transaction.TransactionTests.move
    save_request = transaction.TransactionTests.save_request
    audit = transaction.TransactionTests.audit
    assert_no_remote_changes = transaction.TransactionTests.assert_no_remote_changes

    @contextmanager
    def in_directory(self, directory):
        previous = Path.cwd()
        try:
            os.chdir(directory)
            yield
        finally:
            os.chdir(previous)

    def replay_fixture(self, *, conflicts=("shared.txt",),
                       approved=("shared.txt",), executable=False):
        paths = sorted(set(conflicts) | set(approved))
        for name in paths:
            self.write(name, ("base line for " + name + "\n").encode())
        self.write("unaffected.txt", b"unaffected original line\n")
        self.write("unaffected.bin", bytes(range(256)) + b"\0base binary\xff")
        self.replay_base = self.commit("Base for reviewed resolution\n")
        for name in paths:
            self.write(name, ("custom line for " + name + "\n").encode())
        if executable:
            (self.seed / "shared.txt").chmod(0o755)
        self.write("unaffected.txt", b"unaffected custom text\nsecond custom line\n")
        self.write("unaffected.bin", bytes(reversed(range(256))) + b"\0custom binary\xfe")
        self.original = self.commit(
            "Replay reviewed conflict and independent patches\n\n"
            "Keep the entire message, including Unicode: 雪 and Zoë.\n\n"
            "Reviewed-by: Fixture Reviewer <reviewer@example.invalid>\n\n\n",
            author="Zoë Reviewed Original",
        )
        self.raw_git("checkout", "--quiet", "--detach", self.replay_base)
        for name in conflicts:
            self.write(name, ("upstream line for " + name + "\n").encode())
        self.write("new-upstream.txt", b"unrelated upstream addition\n")
        self.replay_upstream = self.commit("Advance upstream with conflicts\n")
        self.resolved = {}
        files = []
        for name in approved:
            fixture = ".github/custom-sync/compatibility/" + name
            reviewed = ("reviewed combination of upstream and custom: " + name + "\n").encode()
            self.write(fixture, reviewed)
            self.resolved[name] = reviewed
            files.append({
                "path": name,
                "resolved_path": fixture,
                "blob_sha": self.g("hash-object", fixture),
            })
        self.resolution = {
            "branch": "custom-dev",
            "original": self.original,
            "upstream_sha": self.replay_upstream,
            "files": files,
        }
        self.policy = {"resolutions": [self.resolution]}
        self.raw_git("worktree", "add", "--quiet", "--detach",
                     self.work, self.replay_upstream)

    def replay(self, *, policy=None, branch="custom-dev", upstream=None,
               tamper=None):
        real_git = sync.git

        def observed_git(*args, cwd=None):
            self.git_calls.append(args)
            if tamper is not None and "cherry-pick" in args and "--continue" in args:
                tamper(Path(cwd))
            return real_git(*args, cwd=cwd)

        with self.in_directory(self.seed), patch.object(
                sync, "git", side_effect=observed_git):
            return sync.replay_commit(
                self.original, self.work, branch,
                upstream or self.replay_upstream,
                self.policy if policy is None else policy,
            )

    def excluded_patch_id(self, sha, excluded, cwd):
        # Independent raw-Git oracle rather than calling the audited helper.
        diff = self.raw_git(
            "show", "--binary", "--format=", "--no-ext-diff", "--no-textconv",
            sha, "--", ".",
            *[":(literal,exclude)" + path for path in excluded], cwd=cwd,
        )
        result = self.raw_git("patch-id", "--stable", cwd=cwd, data=diff).split()
        return result[0].decode() if result else None

    def assert_replay_fails(self, pattern, **kwargs):
        with self.assertRaisesRegex(
                (RuntimeError, subprocess.CalledProcessError), pattern):
            self.replay(**kwargs)

    def test_scoped_conflict_preserves_metadata_and_unaffected_text_and_binary(self):
        self.replay_fixture()
        try:
            result = self.replay()
        except RuntimeError:
            print("Original raw author/message:", repr(
                self.raw_metadata(self.original, cwd=self.work)))
            print("Replayed raw author/message:", repr(
                self.raw_metadata("HEAD", cwd=self.work)))
            raise
        replayed = result["cherry_pick"]
        self.assertEqual(result["patch_equivalence"], "reviewed-resolution")
        self.assertTrue(result["metadata_preserved"])
        self.assertEqual(result["original"], self.original)
        self.assertEqual(result["resolution"], self.resolution)
        self.assertEqual(self.raw_metadata(self.original, cwd=self.work),
                         self.raw_metadata(replayed, cwd=self.work))
        self.assertEqual((self.work / "shared.txt").read_bytes(),
                         self.resolved["shared.txt"])
        self.assertEqual(self.g("rev-parse", replayed + ":shared.txt", cwd=self.work),
                         self.resolution["files"][0]["blob_sha"])
        for name in ("unaffected.txt", "unaffected.bin"):
            self.assertEqual(
                self.raw_git("show", self.original + ":" + name, cwd=self.work),
                self.raw_git("show", replayed + ":" + name, cwd=self.work),
            )
        before = self.excluded_patch_id(self.original, ["shared.txt"], self.work)
        after = self.excluded_patch_id(replayed, ["shared.txt"], self.work)
        self.assertIsNotNone(before)
        self.assertEqual(before, after)
        self.assertEqual(result["unaffected_patch_id"], before)
        self.assertNotEqual(self.stable_patch_id(self.original, cwd=self.work),
                            self.stable_patch_id(replayed, cwd=self.work))
        self.assertTrue(any("cherry-pick" in args and "--continue" in args
                            for args in self.git_calls))
        self.assertEqual(self.g("status", "--porcelain", cwd=self.work), "")

    def test_nonconflicting_replay_keeps_exact_full_patch_check(self):
        self.replay_fixture(conflicts=())
        result = self.replay(policy={})
        self.assertEqual(result["patch_equivalence"], "exact")
        self.assertEqual(result["patch_id"], result["original_patch_id"])
        self.assertEqual(self.raw_metadata(self.original, cwd=self.work),
                         self.raw_metadata(result["cherry_pick"], cwd=self.work))
        self.assertEqual(self.stable_patch_id(self.original, cwd=self.work),
                         self.stable_patch_id(result["cherry_pick"], cwd=self.work))
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_wrong_branch_cannot_use_resolution(self):
        self.replay_fixture()
        self.assert_replay_fails("(?i)branch/upstream", branch="stable")
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))

    def test_wrong_upstream_cannot_use_resolution(self):
        self.replay_fixture()
        self.assert_replay_fails("(?i)branch/upstream", upstream=self.replay_base)
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))

    def test_wrong_original_does_not_authorize_conflict(self):
        self.replay_fixture()
        self.resolution["original"] = self.replay_base
        self.assert_replay_fails("(?i)unreviewed.*conflict")
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_extra_unapproved_conflict_path_fails_closed(self):
        self.replay_fixture(conflicts=("shared.txt", "extra.txt"))
        self.assert_replay_fails("(?i)unreviewed.*conflict")
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_missing_configured_conflict_path_fails_closed(self):
        self.replay_fixture(approved=("shared.txt", "not-conflicted.txt"))
        self.assert_replay_fails("(?i)unreviewed.*conflict")
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_wrong_pinned_blob_fails_before_cherry_pick(self):
        self.replay_fixture()
        self.resolution["files"][0]["blob_sha"] = "0" * 40
        self.assert_replay_fails("(?i)fixture blob")
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))

    def test_changed_reviewed_fixture_fails_before_cherry_pick(self):
        self.replay_fixture()
        self.write(self.resolution["files"][0]["resolved_path"], b"unreviewed edits\n")
        self.assert_replay_fails("(?i)fixture blob")
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))

    def test_configured_exception_that_applies_cleanly_fails_closed(self):
        self.replay_fixture(conflicts=())
        self.assert_replay_fails("(?i)unexpectedly applied cleanly")
        self.assertTrue(any("cherry-pick" in args for args in self.git_calls))
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_unaffected_text_tampering_is_rejected_after_real_continuation(self):
        self.replay_fixture()

        def tamper(work):
            (work / "unaffected.txt").write_bytes(b"unreviewed text change\n")
            self.raw_git("add", "--", "unaffected.txt", cwd=work)

        self.assert_replay_fails("(?i)outside reviewed conflict files", tamper=tamper)
        self.assertTrue(any("--continue" in args for args in self.git_calls))
        self.assertEqual(self.raw_metadata(self.original, cwd=self.work),
                         self.raw_metadata("HEAD", cwd=self.work))

    def test_unaffected_binary_tampering_is_rejected_after_real_continuation(self):
        self.replay_fixture()

        def tamper(work):
            (work / "unaffected.bin").write_bytes(b"\0\xffunreviewed binary change\x01")
            self.raw_git("add", "--", "unaffected.bin", cwd=work)

        self.assert_replay_fails("(?i)outside reviewed conflict files", tamper=tamper)
        self.assertTrue(any("--continue" in args for args in self.git_calls))
        self.assertEqual(self.raw_metadata(self.original, cwd=self.work),
                         self.raw_metadata("HEAD", cwd=self.work))

    def test_tampered_resolution_after_staging_is_rejected_by_final_blob_check(self):
        self.replay_fixture()

        def tamper(work):
            (work / "shared.txt").write_bytes(b"unreviewed resolved file\n")
            self.raw_git("add", "--", "shared.txt", cwd=work)

        self.assert_replay_fails("(?i)pinned reviewed file", tamper=tamper)

    def test_executable_conflict_is_not_an_ordinary_reviewed_file(self):
        self.replay_fixture(executable=True)
        self.assert_replay_fails("(?i)ordinary reviewed text file")
        self.assertFalse(any("--continue" in args for args in self.git_calls))

    def test_symlink_resolution_fixture_is_rejected(self):
        self.replay_fixture()
        fixture = self.seed / self.resolution["files"][0]["resolved_path"]
        fixture.unlink()
        fixture.symlink_to(self.seed / "shared.txt")
        self.assert_replay_fails("(?i)regular-file resolution fixture")
        self.assertFalse(any("cherry-pick" in args for args in self.git_calls))

    def test_duplicate_original_resolution_is_rejected(self):
        self.replay_fixture()
        self.policy["resolutions"].append(copy.deepcopy(self.resolution))
        self.assert_replay_fails("(?i)duplicate reviewed resolution")

    def test_duplicate_resolution_path_is_rejected(self):
        self.replay_fixture()
        self.resolution["files"].append(copy.deepcopy(self.resolution["files"][0]))
        self.assert_replay_fails("(?i)duplicate paths")

    def test_resolution_fixture_outside_reviewed_directory_is_rejected(self):
        self.replay_fixture()
        self.resolution["files"][0]["resolved_path"] = "shared.txt"
        self.assert_replay_fails("(?i)unsafe or unpinned resolution path")

    def install_transaction_resolution(self):
        config_path = self.work / ".github/custom-sync/branches.json"
        config = json.loads(config_path.read_text())
        relative = ".github/custom-sync/compatibility/shared.txt"
        fixture = self.work / relative
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(b"reviewed shared line preserving upstream and custom\n")
        self.transaction_resolution = {
            "branch": "custom-dev",
            "original": self.originals["custom-dev"][0],
            "upstream_sha": self.upstream_heads["custom-dev"],
            "files": [{
                "path": "shared.txt", "resolved_path": relative,
                "blob_sha": self.g("hash-object", relative, cwd=self.work),
            }],
        }
        config["branches"]["custom-dev"]["resolutions"] = [self.transaction_resolution]
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        self.raw_git("add", ".github/custom-sync", cwd=self.work)
        self.raw_git("commit", "--quiet", "-m", "Review exact conflict fixture",
                     cwd=self.work)
        self.control = self.g("rev-parse", "HEAD", cwd=self.work)
        self.raw_git("push", "--quiet", "origin",
                     "HEAD:refs/heads/" + self.CONTROL, cwd=self.work)
        os.environ["GITHUB_SHA"] = self.control
        self.initial_refs = self.refs()

    def prepare(self, *, failure=None, before_final_validation=None):
        real_git = sync.git
        real_current = sync.ensure_request_current
        validations = []

        def observed_git(*args, cwd=None):
            self.git_calls.append(args)
            if args and args[0] == "push":
                self.pushes.append(args)
            return real_git(*args, cwd=cwd)

        def observed_current(*args, **kwargs):
            validations.append(True)
            if len(validations) == 2 and before_final_validation is not None:
                before_final_validation()
            return real_current(*args, **kwargs)

        with self.in_directory(self.work), \
                patch.object(sync, "git", side_effect=observed_git), \
                patch.object(sync, "ensure_request_current",
                             side_effect=observed_current):
            if failure is None:
                sync.main(prepare_only=True)
            else:
                with self.assertRaisesRegex(
                        (RuntimeError, subprocess.CalledProcessError), failure):
                    sync.main(prepare_only=True)
        return len(validations)

    def assert_prepare_preserved_state(self):
        self.assertEqual(self.pushes, [])
        self.assert_no_remote_changes()
        self.assertEqual((self.work / ".github/custom-sync/state.json").read_bytes(),
                         self.initial_state_file)
        self.assertEqual(self.g("rev-parse", "HEAD", cwd=self.work), self.control)
        self.assertEqual(self.g("diff", "--name-only", cwd=self.work), "")
        self.assertEqual(self.g("diff", "--cached", "--name-only", cwd=self.work), "")
        self.assertFalse(any(ref.startswith("refs/heads/backup/") for ref in self.refs()))

    def test_prepare_only_audits_conflict_and_leaves_remote_local_refs_and_state_unchanged(self):
        self.fixture(conflict="custom-dev")
        self.install_transaction_resolution()
        local_refs = self.g("for-each-ref", "--format=%(refname) %(objectname)",
                            "refs/heads", "refs/tags", cwd=self.work)
        self.assertEqual(self.prepare(), 2)
        self.assert_prepare_preserved_state()
        self.assertEqual(
            self.g("for-each-ref", "--format=%(refname) %(objectname)",
                   "refs/heads", "refs/tags", cwd=self.work),
            local_refs,
        )
        audit = self.audit()
        self.assertEqual(audit["status"], "validated-only")
        self.assertEqual(audit["applied"], [])
        self.assertNotIn("state_commit", audit)
        self.assertEqual({target["branch"] for target in audit["targets"]},
                         set(self.BRANCHES))
        custom = next(target for target in audit["targets"]
                      if target["branch"] == "custom-dev")
        self.assertEqual(custom["mapping"][0]["patch_equivalence"],
                         "reviewed-resolution")
        self.assertEqual(custom["mapping"][0]["resolution"], self.transaction_resolution)
        self.assertEqual(custom["mapping"][1]["patch_equivalence"], "exact")
        for target in audit["targets"]:
            for mapping in target["mapping"]:
                self.assertTrue(mapping["metadata_preserved"])
                self.assertEqual(
                    self.raw_metadata(mapping["original"], cwd=self.work),
                    self.raw_metadata(mapping["cherry_pick"], cwd=self.work),
                )

    def test_prepare_only_still_rejects_unreviewed_conflicts(self):
        self.fixture(conflict="custom-dev")
        self.prepare(failure="(?i)unreviewed.*conflict")
        self.assert_prepare_preserved_state()

    def test_prepare_only_still_checks_independently_expected_final_tree(self):
        self.fixture(conflict="custom-dev")
        self.install_transaction_resolution()
        self.request["targets"][-1]["expected_tree"] = self.tree(self.base)
        self.save_request()
        self.prepare(failure="(?i)final tree")
        self.assert_prepare_preserved_state()

    def test_prepare_only_rechecks_upstream_after_auditing_all_targets(self):
        self.fixture(conflict="custom-dev")
        self.install_transaction_resolution()
        rival = self.remote_commit(
            self.upstream, self.upstream_heads["custom-dev"], "Concurrent upstream",
        )

        def move_upstream():
            self.move(self.upstream, "testing", rival)

        self.assertEqual(self.prepare(
            failure="(?i)upstream moved", before_final_validation=move_upstream,
        ), 2)
        self.assert_prepare_preserved_state()


if __name__ == "__main__":
    unittest.main()
