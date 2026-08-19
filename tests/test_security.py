import tempfile
import time
import unittest
from pathlib import Path

from jarvis.auth_service import AuthError, AuthService
from jarvis.config import Settings
from jarvis.database import Database
from jarvis.security import (
    TokenClaims,
    TokenError,
    decode_token,
    encode_token,
    hash_password,
    verify_password,
)


class SecurityTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        encoded = hash_password("a-strong-password")
        self.assertTrue(verify_password("a-strong-password", encoded))
        self.assertFalse(verify_password("wrong", encoded))
        self.assertNotIn("a-strong-password", encoded)

    def test_token_validation(self):
        claims = TokenClaims("u1", "t1", ("chat",), "access", int(time.time()) + 60, "j1")
        token = encode_token(claims, "x" * 32)
        self.assertEqual(decode_token(token, "x" * 32).subject, "u1")
        with self.assertRaises(TokenError):
            decode_token(token + "x", "x" * 32)

    def test_expired_token_rejected(self):
        token = encode_token(TokenClaims("u", "t", (), "access", 1, "j"), "x" * 32)
        with self.assertRaises(TokenError):
            decode_token(token, "x" * 32)

    def test_refresh_token_replay_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(jwt_secret="x" * 32, database_path=Path(tmp) / "db.sqlite")
            auth = AuthService(Database(settings.database_path), settings)
            auth.register("user@example.com", "long-password-123", "Family")
            tokens = auth.login("user@example.com", "long-password-123")
            auth.rotate(tokens["refresh_token"])
            with self.assertRaises(AuthError):
                auth.rotate(tokens["refresh_token"])

    def test_production_defaults_rejected(self):
        with self.assertRaises(RuntimeError):
            Settings(environment="production").validate()


if __name__ == "__main__":
    unittest.main()
