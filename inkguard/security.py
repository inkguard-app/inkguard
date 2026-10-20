"""
Key custody: one root secret, many derived keys.

Every key the app uses (the pseudonym code table, date offsets, a batch's
seal) is derived from one root secret with HKDF-SHA256 and a distinct label
per purpose, so the keys are cryptographically independent: learning one
reveals nothing about another or about the root.

Randomness comes only from the operating system (os.urandom / secrets).
Never replace it with "random-looking" outside data such as public feeds:
anything an attacker can read, an attacker can reproduce.
"""
import hashlib
import hmac
import os
import secrets


class KeyStore:
    """Root key custody and per-purpose key derivation.

    The root key comes from PHI_ROOT_KEY or is passed in by the caller.
    PHI_ROOT_KEY is best given as 64 hex characters (a value exactly 64
    characters long is read as hex); any other value of at least 32 bytes is
    used as raw bytes, so a passphrase is accepted but is only as strong as
    the passphrase. (The desktop app, in
    development, keeps the key in the operating system's protected store.) If
    neither is given, a random key is made for this process only: anything
    sealed with it cannot be opened after a restart, which makes a missing
    key obvious at once rather than months later.
    """

    def __init__(self, root_key: bytes | None = None):
        env_key = os.getenv("PHI_ROOT_KEY", "")
        self.ephemeral = False
        if root_key is not None:
            self._root = root_key
        elif env_key:
            self._root = bytes.fromhex(env_key) if len(env_key) == 64 else env_key.encode()
        else:
            self._root = secrets.token_bytes(32)
            self.ephemeral = True
        if len(self._root) < 32:
            raise ValueError("PHI_ROOT_KEY must be 64 hex characters or at least 32 bytes")

    def derive(self, purpose: str, length: int = 32) -> bytes:
        """HKDF-SHA256. `purpose` is the info label: change it and you get
        an unrelated key."""
        prk = hmac.new(b"inkguard-hkdf-salt-v1", self._root, hashlib.sha256).digest()
        out, block, counter = b"", b"", 1
        while len(out) < length:
            block = hmac.new(prk, block + purpose.encode() + bytes([counter]), hashlib.sha256).digest()
            out += block
            counter += 1
        return out[:length]

    def __repr__(self) -> str:
        # Key material never reaches a log, a traceback or an error message.
        return "<KeyStore root=REDACTED>"
