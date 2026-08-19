from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


PASSWORD_HASHER = PasswordHasher(
    time_cost=3, memory_cost=65_536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    tenant_id: str
    scopes: tuple[str, ...]
    token_type: str
    expires_at: int
    jti: str


class TokenError(ValueError):
    pass


def encode_token(claims: TokenClaims, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": claims.subject,
                "tenant_id": claims.tenant_id,
                "scopes": list(claims.scopes),
                "type": claims.token_type,
                "exp": claims.expires_at,
                "jti": claims.jti,
            },
            separators=(",", ":"),
        ).encode()
    )
    signature = _b64(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str, expected_type: str = "access") -> TokenClaims:
    try:
        header, payload, signature = token.split(".")
        expected = _b64(
            hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise TokenError("invalid signature")
        data = json.loads(_unb64(payload))
        if data.get("type") != expected_type:
            raise TokenError("wrong token type")
        if int(data["exp"]) <= int(time.time()):
            raise TokenError("token expired")
        return TokenClaims(
            subject=str(data["sub"]),
            tenant_id=str(data["tenant_id"]),
            scopes=tuple(data.get("scopes", ())),
            token_type=str(data["type"]),
            expires_at=int(data["exp"]),
            jti=str(data["jti"]),
        )
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError("malformed token") from exc


def new_claims(
    subject: str, tenant_id: str, scopes: tuple[str, ...], token_type: str, ttl: int
) -> TokenClaims:
    return TokenClaims(
        subject, tenant_id, scopes, token_type, int(time.time()) + ttl, uuid.uuid4().hex
    )


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
