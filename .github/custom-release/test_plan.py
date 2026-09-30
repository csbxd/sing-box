import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import plan

class PlanTests(unittest.TestCase):
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
