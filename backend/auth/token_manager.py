import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from auth.token_store import token_store

logger = logging.getLogger(__name__)

class TokenManager:
    """Provides tools with valid, up-to-date Google credentials."""

    @staticmethod
    def get_valid_credentials(user_id: str = "abhi") -> Credentials | None:
        """
        Loads the encrypted token from SQLite.
        If the access token is expired, it uses the refresh token to get a new one.
        """
        token_data = token_store.get_token(user_id)
        if not token_data:
            logger.warning("[TokenManager] No token found. User must connect Google account.")
            return None

        try:
            # Rehydrate the credentials object
            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_data.get("scopes")
            )

            # If token is expired, refresh it
            if creds.expired and creds.refresh_token:
                logger.info("[TokenManager] Access token expired. Refreshing...")
                creds.refresh(Request())
                
                # Save the new token back to the store
                token_store.save_token(user_id, {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes
                })

            return creds

        except Exception as e:
            logger.error(f"[TokenManager] Failed to get valid credentials: {e}")
            return None

token_manager = TokenManager()