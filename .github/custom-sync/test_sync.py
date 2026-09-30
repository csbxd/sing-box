import unittest
from unittest.mock import patch
import sync

class RequestTests(unittest.TestCase):
    def test_valid(self):
        sync.validate_request({'schema':1,'expected_head':'a'*40,'upstream_sha':'b'*40,'request_id':'20260930-1'})
    def test_sha_injection_rejected(self):
        with self.assertRaises(RuntimeError):
            sync.validate_request({'schema':1,'expected_head':'--force','upstream_sha':'b'*40,'request_id':'x'})
    def test_ref_injection_rejected(self):
        with self.assertRaises(RuntimeError):
            sync.validate_request({'schema':1,'expected_head':'a'*40,'upstream_sha':'b'*40,'request_id':'../custom-dev'})
    def test_bad_tree_rejected(self):
        with self.assertRaises(RuntimeError):
            sync.validate_request({'schema':1,'expected_head':'a'*40,'upstream_sha':'b'*40,'request_id':'x','expected_tree':'HEAD'})

class MultiBranchTests(unittest.TestCase):
    def test_schema2(self):
        targets=sync.validate_request({'schema':2,'request_id':'weekly1','targets':[{'branch':'stable','expected_head':'a'*40,'upstream_sha':'b'*40}]})
        self.assertEqual(targets[0]['branch'],'stable')
    def test_duplicate_rejected(self):
        t={'branch':'stable','expected_head':'a'*40,'upstream_sha':'b'*40}
        with self.assertRaises(RuntimeError):sync.validate_request({'schema':2,'request_id':'x','targets':[t,t]})
    def test_empty_rejected(self):
        with self.assertRaises(RuntimeError):sync.validate_request({'schema':2,'request_id':'x','targets':[]})
    def test_same_head_accepted(self):
        with patch.object(sync.subprocess,'run'),patch.object(sync,'fingerprint',return_value='fp'):
            sync.verify_known_history('stable','a'*40,{'source_sha':'a'*40,'source_fingerprint':'fp'})
    def test_unrecorded_stable_commit_rejected(self):
        with patch.object(sync.subprocess,'run'),patch.object(sync,'fingerprint',return_value='fp'):
            with self.assertRaises(RuntimeError):sync.verify_known_history('stable','b'*40,{'source_sha':'a'*40,'source_fingerprint':'fp'})
    def test_changed_code_rejected(self):
        with patch.object(sync.subprocess,'run'),patch.object(sync,'fingerprint',return_value='new'):
            with self.assertRaises(RuntimeError):sync.verify_known_history('custom-dev','b'*40,{'source_sha':'a'*40,'source_fingerprint':'old'})
    def test_request_only_descendant_accepted(self):
        with patch.object(sync.subprocess,'run'),patch.object(sync,'fingerprint',return_value='fp'),patch.object(sync,'git',side_effect=['c'*40,'c'*40+' '+'a'*40,'.github/custom-release/request.json']):
            sync.verify_known_history('custom-dev','b'*40,{'source_sha':'a'*40,'source_fingerprint':'fp'})
    def test_empty_custom_commit_rejected(self):
        with patch.object(sync.subprocess,'run'),patch.object(sync,'fingerprint',return_value='fp'),patch.object(sync,'git',side_effect=['c'*40,'c'*40+' '+'a'*40,'']):
            with self.assertRaises(RuntimeError):sync.verify_known_history('custom-dev','b'*40,{'source_sha':'a'*40,'source_fingerprint':'fp'})

if __name__ == '__main__': unittest.main()
