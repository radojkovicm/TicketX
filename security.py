"""
Centralne funkcije za rad sa lozinkama.

Novi hešovi koriste werkzeug (PBKDF2-SHA256, soljeno).
Stari nesoljeni SHA-256 hešovi (64 hex karaktera) i dalje se prihvataju
radi kompatibilnosti, ali se pri prvom uspešnom logovanju re-hešuju.
"""
import hashlib
import re
from werkzeug.security import check_password_hash, generate_password_hash

_LEGACY_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


def hash_password(password: str) -> str:
    """Vrati jak, soljeni hash lozinke (za nove/izmenjene lozinke)."""
    return generate_password_hash(password)


def is_legacy_hash(stored_hash: str) -> bool:
    """True ako je hash u starom nesoljenom SHA-256 formatu."""
    return bool(stored_hash) and bool(_LEGACY_SHA256_RE.match(stored_hash))


def verify_password(stored_hash: str, password: str) -> bool:
    """
    Proveri lozinku i protiv novog (werkzeug) i protiv starog (SHA-256) formata.
    """
    if not stored_hash:
        return False
    if is_legacy_hash(stored_hash):
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    try:
        return check_password_hash(stored_hash, password)
    except Exception:
        return False
