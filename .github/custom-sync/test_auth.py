"""Credential-isolation tests with synthetic values and no network access."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import sync


class PushAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.token = 'synthetic-test-value-not-a-credential'
        self.url = 'https://github.com/' + sync.REPO + '.git'
        self.args = ('--atomic', '--force-with-lease=refs/heads/main:' + 'a' * 40,
                     'origin', 'b' * 40 + ':refs/heads/main')

    def test_missing_required_secret_stops_before_any_git_command(self):
        os.environ['CUSTOM_SYNC_REQUIRE_TOKEN'] = '1'
        with patch.object(sync, 'git') as git:
            with self.assertRaisesRegex(RuntimeError, 'CUSTOM_SYNC_TOKEN is missing'):
                sync.main()
            git.assert_not_called()

    def test_token_is_removed_before_preparation_children(self):
        os.environ['CUSTOM_SYNC_TOKEN'] = self.token
        os.environ['CUSTOM_SYNC_REQUIRE_TOKEN'] = '1'
        self.assertEqual(sync.take_push_token(), self.token)
        self.assertNotIn('CUSTOM_SYNC_TOKEN', os.environ)

    def test_required_push_cannot_fall_back_to_other_credentials(self):
        os.environ['CUSTOM_SYNC_REQUIRE_TOKEN'] = '1'
        with patch.object(sync, 'git') as git:
            with self.assertRaisesRegex(RuntimeError, 'CUSTOM_SYNC_TOKEN is missing'):
                sync.atomic_push('', *self.args)
            git.assert_not_called()

    def test_credential_free_local_integration_path_is_unchanged(self):
        with patch.object(sync, 'git', return_value='ok') as git:
            self.assertEqual(sync.atomic_push('', *self.args), 'ok')
            git.assert_called_once_with('push', *self.args)

    def test_wrong_or_multiple_push_destinations_are_rejected(self):
        for url in ('https://example.invalid/repo.git', self.url + '\n' + self.url,
                    self.url.replace('csbxd/', 'SagerNet/')):
            with patch.object(sync, 'git', return_value=url), \
                    patch.object(sync.subprocess, 'check_output') as command:
                with self.assertRaisesRegex(RuntimeError, 'Unexpected push destination'):
                    sync.atomic_push(self.token, *self.args)
                command.assert_not_called()

    def test_non_atomic_push_is_rejected(self):
        with patch.object(sync, 'git', return_value=self.url), \
                patch.object(sync.subprocess, 'check_output') as command:
            with self.assertRaisesRegex(RuntimeError, 'atomic origin'):
                sync.atomic_push(self.token, 'origin', 'HEAD:refs/heads/main')
            command.assert_not_called()

    def test_only_push_receives_secret_and_temporary_helper_is_cleaned(self):
        os.environ.update(GIT_TRACE='1', GIT_TRACE_CURL='1', GIT_CURL_VERBOSE='1',
                          GIT_CONFIG_COUNT='1')
        helper_paths = []
        def fake_push(command, *, env):
            self.assertNotIn(self.token, repr(command))
            self.assertNotIn('CUSTOM_SYNC_TOKEN', os.environ)
            self.assertEqual(env['CUSTOM_SYNC_TOKEN'], self.token)
            self.assertEqual(env['CUSTOM_SYNC_AUTH_URL'], self.url)
            self.assertFalse(any(k.startswith('GIT_TRACE') for k in env))
            self.assertNotIn('GIT_CURL_VERBOSE', env)
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
            self.assertEqual(command[command.index('push') + 1:], list(self.args))
            for setting in ('credential.helper=', 'credential.useHttpPath=true',
                            'http.extraheader=', 'http.https://github.com/.extraheader=',
                            'core.hooksPath=/dev/null', 'http.followRedirects=false'):
                self.assertIn(setting, command)
            helper = Path(env['GIT_ASKPASS'])
            helper_paths.append(helper)
            source = helper.read_text()
            self.assertNotIn(self.token, source)
            compile(source, str(helper), 'exec')
            prompts = [
                ("Username for 'https://github.com/" + sync.REPO + ".git': ", 'x-access-token\n'),
                ("Password for 'https://x-access-token@github.com/" + sync.REPO + ".git': ", self.token + '\n'),
            ]
            for prompt, expected in prompts:
                result = subprocess.run([sys.executable, str(helper), prompt], env=env,
                                        capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, expected)
            for prompt in ("Password for 'https://example.invalid': ",
                           "Password for 'https://x-access-token@github.com/SagerNet/other.git': "):
                result = subprocess.run([sys.executable, str(helper), prompt], env=env,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
            return b'ok\n'
        with patch.object(sync, 'git', return_value=self.url) as git, \
                patch.object(sync.subprocess, 'check_output', side_effect=fake_push):
            self.assertEqual(sync.atomic_push(self.token, *self.args), 'ok')
            git.assert_called_once_with('remote', 'get-url', '--push', '--all', 'origin')
        self.assertTrue(helper_paths)
        self.assertTrue(all(not path.exists() for path in helper_paths))

    def test_push_failure_does_not_include_secret_or_leave_helper(self):
        helpers = []
        def fail(command, *, env):
            helpers.append(Path(env['GIT_ASKPASS']))
            raise subprocess.CalledProcessError(1, command)
        with patch.object(sync, 'git', return_value=self.url), \
                patch.object(sync.subprocess, 'check_output', side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                sync.atomic_push(self.token, *self.args)
        self.assertNotIn(self.token, str(failure.exception))
        self.assertTrue(all(not helper.exists() for helper in helpers))

    def test_unsafe_inherited_build_targets_require_separate_review(self):
        with patch.object(sync, 'git', return_value=self.url), \
                patch.object(sync.subprocess, 'check_output') as command:
            for branch in ('oldstable', 'unstable'):
                with self.assertRaisesRegex(RuntimeError, 'Inherited Build trigger'):
                    sync.atomic_push(self.token, '--atomic', 'origin',
                                     'b' * 40 + ':refs/heads/' + branch)
            command.assert_not_called()

    def test_checkout_origin_without_dot_git_is_supported(self):
        url = self.url.removesuffix('.git')
        def fake_push(command, *, env):
            self.assertEqual(env['CUSTOM_SYNC_AUTH_URL'], url)
            helper = env['GIT_ASKPASS']
            prompt = "Password for '" + url.replace('https://', 'https://x-access-token@', 1) + "': "
            result = subprocess.run([sys.executable, helper, prompt], env=env,
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, self.token + '\n')
            return b'ok'
        with patch.object(sync, 'git', return_value=url), \
                patch.object(sync.subprocess, 'check_output', side_effect=fake_push):
            self.assertEqual(sync.atomic_push(self.token, *self.args), 'ok')
