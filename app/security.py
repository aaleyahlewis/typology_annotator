import hashlib
import hmac
import secrets


def hash_code(code, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_code(code, stored):
    try:
        salt, digest = stored.split(":")
        return hmac.compare_digest(hash_code(code, bytes.fromhex(salt)).split(":")[1], digest)
    except ValueError:
        return False


def session_stamp(code_hash):
    """Changes when a participant's code is reset, which signs out their old sessions."""
    return hashlib.sha256(code_hash.encode()).hexdigest()[:16]


def new_access_code():
    return "-".join(secrets.token_hex(3) for _ in range(3))
