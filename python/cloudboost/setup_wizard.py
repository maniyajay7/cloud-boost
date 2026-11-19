"""
cloud-boost v3 — setup wizard
Guides user through creating a custom Google OAuth Client ID for 5-10x faster uploads.
"""

from colorama import Fore, Style, init

init(autoreset=True)

SETUP_GUIDE = f"""
{Fore.CYAN}{'═' * 60}
  ⚡ cloud-boost — Custom OAuth Client ID Setup
  This is the SINGLE BIGGEST speed improvement you can make.
  Going from shared rclone client ID → your own = 5-10x faster.
{'═' * 60}{Style.RESET_ALL}

{Fore.YELLOW}Step 1:{Style.RESET_ALL} Go to Google Cloud Console
   → https://console.cloud.google.com/

{Fore.YELLOW}Step 2:{Style.RESET_ALL} Create a new project (or use existing)
   → Click "Select Project" → "New Project" → Name it "cloud-boost"

{Fore.YELLOW}Step 3:{Style.RESET_ALL} Enable Google Drive API
   → APIs & Services → Library → Search "Google Drive API" → Enable

{Fore.YELLOW}Step 4:{Style.RESET_ALL} Create OAuth Consent Screen
   → APIs & Services → OAuth consent screen
   → User Type: "External" → Create
   → App name: "cloud-boost" → Your email → Save
   → Scopes: Add "drive" scope → Save
   → Test Users: Add your Gmail address → Save

{Fore.YELLOW}Step 5:{Style.RESET_ALL} Create OAuth Client ID
   → APIs & Services → Credentials → Create Credentials → OAuth Client ID
   → Application type: "Desktop app"
   → Name: "cloud-boost"
   → Download JSON → Save as "client_id.json"

{Fore.YELLOW}Step 6:{Style.RESET_ALL} Configure rclone with YOUR client ID
   → Run: rclone config
   → Edit your Google Drive remote
   → Set client_id and client_secret from your downloaded JSON
   → Re-authorize when prompted

{Fore.GREEN}Done! Your uploads will now be 5-10x faster because you're
no longer sharing bandwidth with thousands of other rclone users.{Style.RESET_ALL}

{Fore.CYAN}{'═' * 60}{Style.RESET_ALL}
"""


def print_setup_guide() -> None:
    """Print the OAuth setup guide."""
    print(SETUP_GUIDE)


def check_custom_client_id() -> bool:
    """
    Check if user has a custom client ID configured in rclone.
    Returns True if custom, False if using shared default.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["rclone", "config", "dump"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import json
            config = json.loads(result.stdout)
            for remote_name, remote_config in config.items():
                if remote_config.get("type") == "drive":
                    client_id = remote_config.get("client_id", "")
                    if client_id and len(client_id) > 10:
                        return True  # Has custom client ID
            return False  # Using shared default
    except Exception:
        pass
    return False
