import unittest
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

if __name__ == '__main__': unittest.main()
