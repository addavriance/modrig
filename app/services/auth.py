from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass


def offline_uuid(username: str) -> uuid.UUID:
    """Replicates Java's UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(UTF_8)),
    which vanilla clients/servers use to derive a stable UUID for offline-mode players."""

    data = f"OfflinePlayer:{username}".encode("utf-8")

    digest = bytearray(hashlib.md5(data).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant

    return uuid.UUID(bytes=bytes(digest))


@dataclass
class AuthProfile:
    username: str
    uuid: str
    access_token: str
    user_type: str = "legacy"


def build_offline_profile(username: str) -> AuthProfile:
    return AuthProfile(
        username=username,
        uuid=str(offline_uuid(username)),
        access_token=secrets.token_hex(16),
        user_type="legacy",
    )
