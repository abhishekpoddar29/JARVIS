import sqlite3
import json
import logging
from pathlib import Path
from cryptography.fernet import Fernet
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "jarvis_auth.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Initialize encryption cipher
try:
    cipher = Fernet(settings.token_encryption_key.encode())
except Exception as e:
    logger.error("TOKEN_ENCRYPTION_KEY must be a valid 32-byte URL-safe base64 string.")
    raise

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            user_id TEXT PRIMARY KEY,
            token_data BLOB
        )
    """)
    return conn

class TokenStore:
    """Securely stores and retrieves encrypted OAuth tokens."""

    @staticmethod
    def save_token(user_id: str, token_data: dict) -> None:
        """Encrypts and saves the token data (usually the refresh token)."""
        json_data = json.dumps(token_data).encode('utf-8')
        encrypted_data = cipher.encrypt(json_data)
        
        conn = _get_db()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO oauth_tokens (user_id, token_data) VALUES (?, ?)",
                    (user_id, encrypted_data)
                )
            logger.info(f"[TokenStore] Token saved securely for user: {user_id}")
        finally:
            conn.close()

    @staticmethod
    def get_token(user_id: str = "abhi") -> dict | None:
        """Retrieves and decrypts the token data."""
        conn = _get_db()
        try:
            with conn:
                cursor = conn.execute(
                    "SELECT token_data FROM oauth_tokens WHERE user_id = ?",
                    (user_id,)
                )
                row = cursor.fetchone()
                
            if row:
                decrypted_data = cipher.decrypt(row[0])
                return json.loads(decrypted_data.decode('utf-8'))
            return None
        except Exception as e:
            logger.error(f"[TokenStore] Failed to retrieve token: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def delete_token(user_id: str = "abhi") -> None:
        """Deletes the token (used for revoking access)."""
        conn = _get_db()
        try:
            with conn:
                conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
            logger.info(f"[TokenStore] Token deleted for user: {user_id}")
        finally:
            conn.close()

token_store = TokenStore()