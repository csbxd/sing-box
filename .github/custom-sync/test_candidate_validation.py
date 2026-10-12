import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('candidate_validation', Path(__file__).with_name('candidate_validation.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CandidateSelectionTests(unittest.TestCase):
    def fixtures(self, custom=False):
        sha = 'a' * 40
        target = {'branch': 'custom-dev' if custom else 'testing', 'upstream_sha': 'b' * 40, 'expected_head': sha}
        row = {**target, 'skip': False, 'source_sha': sha, 'source_tree': 'c' * 40,
               'replay_commits': ['d' * 40],
               'mapping': [{'original': 'd' * 40, 'metadata_preserved': True}]}
        return ({'schema': 2, 'status': 'validated-only', 'control_commit': sha,
                 'request_id': 'test', 'targets': [row]},
                {'request_id': 'test', 'targets': [target]}, sha)

    def test_subset_absence_of_custom_is_explicit(self):
        audit, request, sha = self.fixtures()
        custom, subset = module.select(audit, request, sha)
        self.assertEqual(custom, '')
        self.assertEqual([r['branch'] for r in subset], ['testing'])

    def test_custom_keeps_all_existing_checks_selected(self):
        audit, request, sha = self.fixtures(custom=True)
        self.assertEqual(module.select(audit, request, sha), (sha, []))

    def test_noop_is_not_rebuilt(self):
        audit, request, sha = self.fixtures()
        audit['targets'][0] = {'branch': 'testing', 'source_sha': sha, 'skip': True}
        self.assertEqual(module.select(audit, request, sha), ('', []))

    def test_fail_closed(self):
        audit, request, sha = self.fixtures()
        mutations = [
            lambda a: a.update(status='prepared'),
            lambda a: a.update(control_commit='e' * 40),
            lambda a: a.update(request_id='other'),
            lambda a: a.update(targets=[]),
            lambda a: a['targets'].append(copy.deepcopy(a['targets'][0])),
            lambda a: a['targets'][0].update(source_sha='bad'),
            lambda a: a['targets'][0].update(source_tree='bad'),
            lambda a: a['targets'][0].update(upstream_sha='e' * 40),
            lambda a: a['targets'][0].update(skip='false'),
            lambda a: a['targets'][0].update(mapping=[]),
            lambda a: a['targets'][0]['mapping'][0].update(metadata_preserved=False),
        ]
        for mutate in mutations:
            candidate = copy.deepcopy(audit)
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                module.select(candidate, request, sha)


if __name__ == '__main__':
    unittest.main()
