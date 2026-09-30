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
            plan.nearest_release([], 'HEAD')
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

    def test_unreachable_release_rejected(self):
        with patch.object(plan,'run',return_value='a'*40), patch.object(plan.subprocess,'run') as proc:
            proc.return_value.returncode=1
            with self.assertRaises(RuntimeError):
                plan.nearest_release([{'draft':False,'tag_name':'v9.0.0'}],'HEAD')

    def test_nearest_includes_prerelease(self):
        items = [{'draft':False,'tag_name':'v1.0.0'}, {'draft':False,'tag_name':'v1.1.0-alpha.1','prerelease':True}]
        def fake(*args):
            if args[1]=='rev-parse': return args[2]
            return '8' if 'v1.0.0' in args[-1] else '2'
        with patch.object(plan, 'run', side_effect=fake), patch.object(plan.subprocess, 'run') as proc:
            proc.return_value.returncode = 0
            self.assertEqual(plan.nearest_release(items,'HEAD')['tag_name'],'v1.1.0-alpha.1')

if __name__ == '__main__': unittest.main()
