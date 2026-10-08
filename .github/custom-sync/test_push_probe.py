import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import push_probe as probe


class PushProbeTests(unittest.TestCase):
    def request(self):
        return {'schema': 1, 'request_id': '20261008-proof', 'code_commit': 'a' * 40}

    def test_strict_request_and_isolated_refs(self):
        self.assertEqual(probe.validate_request(self.request()), [
            'refs/heads/permission-probe/20261008-proof/content',
            'refs/heads/permission-probe/20261008-proof/workflow'])
        for key, value in [('schema', True), ('schema', 2), ('request_id', '../custom-dev'),
                           ('request_id', 'x\nrefs/heads/main'), ('code_commit', 'main')]:
            request = self.request()
            request[key] = value
            with self.assertRaises(RuntimeError):
                probe.validate_request(request)
        request = self.request()
        request['destination'] = 'main'
        with self.assertRaises(RuntimeError):
            probe.validate_request(request)

    def test_push_has_atomic_exact_leases_and_only_probe_destinations(self):
        refs = probe.validate_request(self.request())
        args = probe.push_args(refs, ['', ''], ['a' * 40, 'b' * 40])
        self.assertEqual(args, ['--atomic',
            '--force-with-lease=' + refs[0] + ':', '--force-with-lease=' + refs[1] + ':',
            'origin', 'a' * 40 + ':' + refs[0], 'b' * 40 + ':' + refs[1]])
        args = probe.push_args(refs, ['a' * 40] * 2, ['b' * 40, 'c' * 40])
        self.assertIn('--force-with-lease=' + refs[1] + ':' + 'a' * 40, args)
        for destinations in ([refs[0], 'refs/heads/main'], [refs[0], refs[0]], refs[:1]):
            with self.assertRaises(RuntimeError):
                probe.push_args(destinations, ['', ''], ['a' * 40, 'b' * 40])

    def test_remote_changes_fail_closed(self):
        with mock.patch.object(probe, 'remote_snapshot', return_value={'refs/heads/main': 'b' * 40}):
            with self.assertRaises(RuntimeError):
                probe.require_snapshot({'refs/heads/main': 'a' * 40})

    def test_real_git_fixtures_and_atomic_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, remote = root / 'work', root / 'remote.git'
            subprocess.check_call(['git', 'init', '-q', str(work)])
            subprocess.check_call(['git', 'init', '-q', '--bare', str(remote)])
            probe.git('remote', 'add', 'origin', str(remote), cwd=work)
            initial = probe.make_commit('20261008-proof', 'contents-create', cwd=work)
            plain = probe.make_commit('20261008-proof', 'contents-update', parent=initial, cwd=work)
            workflow = probe.make_commit('20261008-proof', 'workflow-update', parent=initial,
                                         with_workflow=True, cwd=work)
            self.assertEqual(probe.git('ls-tree', '-r', '--name-only', initial, cwd=work), 'PROBE.md')
            self.assertEqual(subprocess.check_output(
                ['git', 'show', workflow + ':' + probe.WORKFLOW_PATH], cwd=work).decode(), probe.INERT_WORKFLOW + '# Unique request: 20261008-proof\n')
            self.assertIn('workflow_dispatch:', probe.INERT_WORKFLOW)
            for forbidden in ('push:', 'pull_request:', 'schedule:', 'uses:', 'secrets.', 'curl', 'wget'):
                self.assertNotIn(forbidden, probe.INERT_WORKFLOW)
            self.assertIn('permissions: {}', probe.INERT_WORKFLOW)
            self.assertIn('if: ${{ false }}', probe.INERT_WORKFLOW)
            refs = probe.validate_request(self.request())
            probe.git('push', *probe.push_args(refs, ['', ''], [initial, initial]), cwd=work)
            # Existing refs cannot be overwritten by a repeated create-only probe.
            with self.assertRaises(subprocess.CalledProcessError):
                probe.git('push', *probe.push_args(refs, ['', ''], [plain, workflow]), cwd=work)
            # An incorrect lease rejects the entire transaction.
            with self.assertRaises(subprocess.CalledProcessError):
                probe.git('push', *probe.push_args(refs, [initial, 'f' * 40], [plain, workflow]), cwd=work)
            for ref in refs:
                self.assertEqual(probe.git('rev-parse', ref, cwd=remote), initial)
            probe.git('push', *probe.push_args(refs, [initial, initial], [plain, workflow]), cwd=work)
            self.assertEqual(probe.git('rev-parse', refs[0], cwd=remote), plain)
            self.assertEqual(probe.git('rev-parse', refs[1], cwd=remote), workflow)


if __name__ == '__main__':
    unittest.main()
