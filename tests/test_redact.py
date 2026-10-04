import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from redact import redact  # noqa: E402


class RedactTest(unittest.TestCase):
    def assertMasked(self, text, secret):
        out = redact(text)
        self.assertNotIn(secret, out)
        self.assertIn("[REDACTED]", out)

    def test_github_tokens(self):
        self.assertMasked("token ghp_" + "a" * 36, "ghp_" + "a" * 36)
        self.assertMasked("github_pat_" + "B1" * 20, "github_pat_" + "B1" * 20)

    def test_provider_keys(self):
        self.assertMasked("key=sk-ant-api03-" + "x" * 40, "x" * 40)
        self.assertMasked("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP")
        self.assertMasked("sb_secret_" + "z" * 30, "z" * 30)
        self.assertMasked("xoxb-1234-5678-abcdefghij", "abcdefghij")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJlLXZhbHVl"
        self.assertMasked("Authorization: Bearer " + jwt, jwt)

    def test_url_credentials(self):
        self.assertMasked("postgres://app:hunter22@db.example.com:5432/x", "hunter22")

    def test_named_assignments(self):
        self.assertMasked("SUPABASE_SERVICE_ROLE_KEY=abc123def456", "abc123def456")
        self.assertMasked('"password": "letmein-please"', "letmein-please")

    def test_private_key_block(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertMasked("x\n" + block + "\ny", "MIIEow")

    def test_plain_text_untouched(self):
        text = "FAIL tests/test_auth.py::test_login - AssertionError: 401 != 200"
        self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
