import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import plan

class PlanTests(unittest.TestCase):
    def test_releases_paginates_small_pages(self):
        with patch.object(plan,'run',side_effect=[json.dumps([{}]*10),json.dumps([{'tag_name':'v1.0.0'}])]) as fetch:
            self.assertEqual(len(plan.releases('owner/repo')),11)
            self.assertIn('per_page=10&page=2',fetch.call_args.args[-1])
    def test_releases_retries_transient_failure(self):
        error=plan.subprocess.CalledProcessError(1,['gh','api'])
        with patch.object(plan,'run',side_effect=[error,'[]']) as fetch, patch.object(plan.time,'sleep') as sleep:
            self.assertEqual(plan.releases('owner/repo'),[])
            self.assertEqual(fetch.call_count,2)
            sleep.assert_called_once_with(2)
    def test_releases_fails_closed(self):
        error=plan.subprocess.CalledProcessError(1,['gh','api'])
        with patch.object(plan,'run',side_effect=error) as fetch, patch.object(plan.time,'sleep'):
            with self.assertRaises(plan.subprocess.CalledProcessError):plan.releases('owner/repo')
            self.assertEqual(fetch.call_count,4)

    def test_stable(self):
        self.assertEqual(plan.custom_tag('v1.14.0', 1), 'v1.14.0-c1')
    def test_prerelease(self):
        self.assertEqual(plan.custom_tag('v1.15.0-alpha.9', 12), 'v1.15.0-alpha.9.c12')
    def test_increment(self):
        self.assertEqual(plan.next_revision('v1.15.0-alpha.9', ['v1.15.0-alpha.9.c1','v1.15.0-alpha.9.c10','v1.15.0-alpha.8.c99']), 11)
    def test_reject_injection(self):
        with self.assertRaises(ValueError):
            plan.custom_tag('1.2.3\nmalicious=true', 1)
    def test_marker(self):
        self.assertEqual(plan.metadata({'body': '<!-- custom-core-release: {"source_tree":"abc"} -->'}), {'source_tree':'abc'})
    def test_no_release(self):
        with self.assertRaises(RuntimeError):
            plan.select_series_release([], '1.15.0-alpha.9')
    def test_source_version(self):
        self.assertEqual(plan.source_version('---\nicon: sample\n---\n#### 1.15.0-alpha.9\n#### 1.14.2'), '1.15.0-alpha.9')
    def test_source_version_missing(self):
        with self.assertRaises(RuntimeError): plan.source_version('no release headings')
    def test_latest_series_prerelease(self):
        items=[{'draft':False,'tag_name':t} for t in ['v1.14.2','v1.15.0-alpha.9','v1.15.0-alpha.10','v1.16.0-alpha.1']]
        self.assertEqual(plan.select_series_release(items,'1.15.0-alpha.9')['tag_name'],'v1.15.0-alpha.10')
    def test_stable_outranks_prerelease(self):
        items=[{'draft':False,'tag_name':t} for t in ['v1.15.0','v1.15.0-rc.99']]
        self.assertEqual(plan.select_series_release(items,'1.15.0-alpha.9')['tag_name'],'v1.15.0')
    def test_draft_excluded(self):
        items=[{'draft':True,'tag_name':'v1.15.0'}, {'draft':False,'tag_name':'v1.15.0-rc.1'}]
        self.assertEqual(plan.select_series_release(items,'1.15.0-alpha.9')['tag_name'],'v1.15.0-rc.1')

    def test_push_missing_request_skips_without_api(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {'GITHUB_REPOSITORY':'csbxd/sing-box',
                                            'GITHUB_OUTPUT':'output', 'GITHUB_EVENT_NAME':'push'}), \
                     patch.object(plan, 'run', return_value='a'*40), \
                     patch.object(plan, 'releases') as api:
                    plan.main()
                    api.assert_not_called()
                self.assertEqual(Path('output').read_text(), 'skip=true\n')
            finally:
                os.chdir(original)

    def test_push_invalid_existing_request_fails(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                request = Path('.github/custom-release/request.json')
                request.parent.mkdir(parents=True)
                for content in ('invalid json', '{}', '{"schema":1,"source_sha":"bad"}'):
                    request.write_text(content)
                    with patch.dict(os.environ, {'GITHUB_REPOSITORY':'csbxd/sing-box',
                                                'GITHUB_OUTPUT':'output', 'GITHUB_EVENT_NAME':'push'}), \
                         patch.object(plan, 'run', return_value='a'*40), \
                         patch.object(plan, 'releases') as api:
                        with self.assertRaises((RuntimeError, json.JSONDecodeError)):
                            plan.main()
                        api.assert_not_called()
                self.assertFalse(Path('output').exists())
            finally:
                os.chdir(original)

    def test_unchanged_tree_skips_without_upstream_fetch(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                existing = [{'draft': False, 'html_url': 'https://example/release',
                             'body': '<!-- custom-core-release: {"source_tree":"tree"} -->'}]
                with patch.dict(os.environ, {'GITHUB_REPOSITORY':'csbxd/sing-box',
                                            'GITHUB_OUTPUT':'output', 'GITHUB_EVENT_NAME':'workflow_dispatch'}), \
                     patch.object(plan, 'run', side_effect=['a'*40,'tree']), \
                     patch.object(plan, 'releases', return_value=existing), \
                     patch.object(plan.subprocess, 'check_output', return_value=b'100644 blob abc\tfile\0'):
                    plan.main()
                self.assertTrue(json.loads(Path('release-plan.json').read_text())['skip'])
                self.assertEqual(Path('output').read_text(), 'skip=true\n')
            finally:
                os.chdir(original)

if __name__ == '__main__': unittest.main()
