import logging
from google_auth_oauthlib.flow import Flow
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Scopes define what Jarvis is allowed to do
SCOPES = [
    "https://www.googleapis.com/auth/calendar", 
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly"
]

def _get_client_config():
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri]
        }
    }

class GoogleOAuth:
    """Manages the Google OAuth2 flow."""
    
    # Store the flow in memory during the redirect dance
    _flow = None

    @staticmethod
    def get_auth_url() -> str:
        """Generates the Google Login URL for the user to click."""
        flow = Flow.from_client_config(
            client_config=_get_client_config(),
            scopes=SCOPES,
            redirect_uri=settings.google_redirect_uri
        )
        
        # Save flow to memory so we can use it in the callback
        GoogleOAuth._flow = flow
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',        # Required to get a refresh token
            prompt='consent',             # Force consent to ensure refresh token is returned
            include_granted_scopes='true'
        )
        return auth_url

    @staticmethod
    def exchange_code_for_token(code: str) -> dict:
        """Exchanges the callback code for Access and Refresh tokens."""
        if not GoogleOAuth._flow:
            raise RuntimeError("OAuth flow not initialized. Please restart the login process.")
            
        try:
            # Use the exact same flow object that generated the URL
            GoogleOAuth._flow.fetch_token(code=code)
            credentials = GoogleOAuth._flow.credentials
            
            return {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes
            }
        except Exception as e:
            logger.error(f"[GoogleOAuth] Failed to exchange code: {e}")
            raise

google_oauth = GoogleOAuth()