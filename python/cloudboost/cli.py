"""
cloud-boost v3 CLI
Production CLI with entropy-aware compression, quota tracking, setup wizard.
Interactive mode when run without arguments.
"""

import sys
import os
import click
from colorama import Fore, Style, init

from cloudboost.config import (
    UploadConfig,
    parse_size,
    format_size,
    save_default_config,
    load_config,
    ensure_dirs,
    PROVIDERS,
    MB,
)
from cloudboost.uploader import upload_file, upload_folder
from cloudboost.transport import (
    check_rclone_installed,
    get_rclone_remotes,
    normalize_destination,
    QuotaTracker,
)
from cloudboost.setup_wizard import print_setup_guide, check_custom_client_id
from cloudboost.compressor import get_compressor_name
from cloudboost.manifest import list_pending_uploads
from cloudboost.logger import get_logger

init(autoreset=True)

BANNER = f"""{Fore.CYAN}
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST
{Fore.WHITE}  ⚡ v3.0 — Production Upload Engine
  ⚡ Entropy-aware | zstd | Quota-safe | Auto-tuned
{Style.RESET_ALL}"""

SEP = "─" * 50


# ─── Interactive Wizard ──────────────────────────────────────────────────────

def _prompt_input(prompt: str, default: str = "") -> str:
    """Styled input prompt with optional default."""
    if default:
        display = f"  {Fore.CYAN}→ {prompt} {Fore.LIGHTBLACK_EX}[{default}]{Style.RESET_ALL}: "
    else:
        display = f"  {Fore.CYAN}→ {prompt}{Style.RESET_ALL}: "
    value = input(display).strip()
    return value if value else default


def _prompt_choice(prompt: str, options: list[dict], default_idx: int = 0) -> dict:
    """
    Display a numbered list of options and return the chosen one.
    Each option is a dict with 'label', 'value', and optional 'desc'.
    """
    print(f"\n  {Fore.CYAN}{prompt}{Style.RESET_ALL}")
    print(f"  {Fore.LIGHTBLACK_EX}{SEP}{Style.RESET_ALL}")

    for i, opt in enumerate(options):
        marker = f"{Fore.GREEN}●" if i == default_idx else f"{Fore.LIGHTBLACK_EX}○"
        label = opt["label"]
        desc = opt.get("desc", "")
        default_tag = f" {Fore.YELLOW}← default{Style.RESET_ALL}" if i == default_idx else ""
        desc_text = f" {Fore.LIGHTBLACK_EX}— {desc}{Style.RESET_ALL}" if desc else ""
        print(f"  {marker} {Fore.WHITE}{i + 1}. {label}{Style.RESET_ALL}{desc_text}{default_tag}")

    print(f"  {Fore.LIGHTBLACK_EX}{SEP}{Style.RESET_ALL}")

    while True:
        raw = input(f"  {Fore.CYAN}→ Choose [1-{len(options)}]{Style.RESET_ALL}: ").strip()
        if not raw:
            return options[default_idx]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"  {Fore.RED}  Invalid — enter a number 1-{len(options)}{Style.RESET_ALL}")


def _prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Yes/No prompt."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {Fore.CYAN}→ {prompt} [{hint}]{Style.RESET_ALL}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def interactive_upload():
    """Simple, user-friendly interactive upload wizard."""
    print(BANNER)

    remotes = get_rclone_remotes()
    if not remotes:
        print(f"  {Fore.RED}✗ No cloud storage configured in rclone.{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}  Please run 'rclone config' first to connect your Google Drive.{Style.RESET_ALL}\n")
        sys.exit(1)

    # ─── Step 1: Remote Selection (auto if only 1 configured) ─────────────────
    if len(remotes) == 1:
        remote = remotes[0]
        provider = "gdrive" if "gdrive" in remote.lower() else "generic"
        remote_display = "Google Drive" if "gdrive" in remote.lower() else remote.rstrip(':')
        print(f"  {Fore.GREEN}✓ Target Cloud : {Fore.WHITE}{remote_display} ({remote}){Style.RESET_ALL}\n")
    else:
        # Multiple configured remotes - show ONLY the configured ones!
        options = [
            {"label": r, "value": "gdrive" if "gdrive" in r.lower() else "generic", "remote": r}
            for r in remotes
        ]
        chosen = _prompt_choice("Select Cloud Storage", options)
        remote = chosen["remote"]
        provider = chosen["value"]
        print()

    # ─── Step 2: Source File or Folder ───────────────────────────────────────
    while True:
        source_raw = input(f"  {Fore.CYAN}→ Enter file or folder path{Style.RESET_ALL}: ").strip()
        if not source_raw:
            print(f"  {Fore.RED}  Path cannot be empty{Style.RESET_ALL}")
            continue
        source = source_raw.strip('"').strip("'")
        if os.path.exists(source):
            if os.path.isdir(source):
                count = sum(1 for f in os.scandir(source) if f.is_file())
                print(f"  {Fore.GREEN}  ✓ Folder verified — {count} file(s){Style.RESET_ALL}\n")
            else:
                size = os.path.getsize(source)
                print(f"  {Fore.GREEN}  ✓ File verified — {format_size(size)}{Style.RESET_ALL}\n")
            break
        else:
            print(f"  {Fore.RED}  ✗ File or folder not found: {source}{Style.RESET_ALL}")
            print(f"  {Fore.YELLOW}    Tip: You can drag & drop the file directly into this terminal window.{Style.RESET_ALL}")

    # ─── Step 3: Destination Folder on Cloud ──────────────────────────────────
    dest_input = input(f"  {Fore.CYAN}→ Enter folder name on cloud {Fore.LIGHTBLACK_EX}[Backup]{Style.RESET_ALL}: ").strip()
    dest_input = dest_input.strip('"').strip("'")
    if not dest_input:
        dest_input = "Backup"

    # Normalize destination (handles Z:\Movie, "Z:\Movie", Movie, gdrive:/Movie)
    destination = normalize_destination(dest_input, default_remote=remote)
    print(f"  {Fore.GREEN}  ✓ Target location: {Fore.WHITE}{destination}{Style.RESET_ALL}\n")

    # ─── Step 4: Simple Confirmation ──────────────────────────────────────────
    confirm = input(f"  {Fore.CYAN}→ Start transfer now? {Fore.LIGHTBLACK_EX}[Y/n]{Style.RESET_ALL}: ").strip().lower()
    if confirm in ("n", "no"):
        print(f"\n  {Fore.YELLOW}Transfer cancelled.{Style.RESET_ALL}\n")
        sys.exit(0)

    # By default, do NOT compress — send original file as-is!
    config = UploadConfig(
        provider=provider,
        compression=False,
        dedup=True,
    )
    quota = QuotaTracker(provider=provider)

    print()
    if os.path.isdir(source):
        success = upload_folder(source, destination, config, quota)
    else:
        success = upload_file(source, destination, config, quota)

    sys.exit(0 if success else 1)


# ─── CLI Commands ────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version="3.0.0")
def main():
    """⚡ cloud-boost — supercharge your cloud uploads"""
    ensure_dirs()
    save_default_config()


@main.command()
@click.argument("source", required=False, default=None)
@click.argument("destination", required=False, default=None)
@click.option("--threads", "-t", default=None, type=int, help="Upload threads (auto-tuned)")
@click.option("--no-compress", is_flag=True, default=False, help="Skip compression entirely")
@click.option("--no-dedup", is_flag=True, default=False, help="Disable deduplication")
@click.option("--max-bw", default=None, help="Bandwidth limit e.g. 50MB")
@click.option("--max-transfer", default=None, help="Max bytes to upload e.g. 700GB (quota safety)")
@click.option("--provider", "-p", default="gdrive", help="Cloud provider")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose output")
def upload(source, destination, threads, no_compress, no_dedup, max_bw, max_transfer, provider, verbose):
    """Upload FILE or FOLDER to cloud DESTINATION.

    \b
    Run without arguments for interactive mode:
      cloudboost upload

    \b
    Or provide source and destination directly:
      cloudboost upload myfile.zip gdrive:/backup/
      cloudboost upload ./folder gdrive:/data/ --no-compress
      cloudboost upload huge.iso gdrive:/iso/ --max-bw 50MB
    """
    # Interactive mode when no args
    if source is None or destination is None:
        interactive_upload()
        return

    print(BANNER)

    # Check custom client ID
    if provider == "gdrive" and not check_custom_client_id():
        print(f"{Fore.YELLOW}  ⚠ You're using rclone's SHARED client ID.")
        print(f"{Fore.YELLOW}    This is 5-10x SLOWER than a custom one.")
        print(f"{Fore.YELLOW}    Run 'cloudboost setup' for the speed fix.\n")

    overrides = {
        "provider": provider,
        "verbose": verbose,
        "compression": not no_compress,
        "dedup": not no_dedup,
    }
    if threads is not None:
        overrides["rclone_transfers"] = threads
    if max_bw is not None:
        overrides["max_bandwidth"] = parse_size(max_bw)
    if max_transfer is not None:
        overrides["max_transfer"] = parse_size(max_transfer)

    config = UploadConfig(**{k: v for k, v in overrides.items() if v is not None})
    quota = QuotaTracker(provider=provider)

    if os.path.isdir(source):
        success = upload_folder(source, destination, config, quota)
    else:
        success = upload_file(source, destination, config, quota)

    sys.exit(0 if success else 1)


@main.command()
def resume():
    """Show and resume interrupted uploads."""
    print(BANNER)
    pending = list_pending_uploads()

    if not pending:
        print(f"{Fore.GREEN}  No pending uploads found. All clean!\n")
        return

    print(f"{Fore.YELLOW}  Found {len(pending)} interrupted upload(s):\n")
    for i, m in enumerate(pending, 1):
        status = m.get("status", "unknown")
        color = Fore.YELLOW if status == "uploading" else Fore.RED
        print(f"  {i}. {color}[{status.upper()}] {m.get('source_name', '?')}")
        print(f"     Size: {format_size(m.get('source_size', 0))}")
        print(f"     Dest: {m.get('destination', '?')}")
        if m.get("error"):
            print(f"     Error: {m['error'][:80]}")
        print()

    print(f"  → Re-run 'cloudboost upload <source> <dest>' to resume automatically.\n")


@main.command()
def doctor():
    """Diagnose cloud-boost setup — rclone, remotes, compressor, client ID."""
    print(BANNER)
    print(f"{Fore.CYAN}→ Running diagnostics...\n")

    all_ok = True

    # rclone
    if check_rclone_installed():
        print(f"{Fore.GREEN}  ✓ rclone installed")
    else:
        print(f"{Fore.RED}  ✗ rclone NOT found — https://rclone.org/install/")
        all_ok = False

    # Remotes
    remotes = get_rclone_remotes()
    if remotes:
        print(f"{Fore.GREEN}  ✓ {len(remotes)} remote(s) configured:")
        for r in remotes:
            print(f"    · {r}")
    else:
        print(f"{Fore.YELLOW}  ⚠ No remotes — run 'rclone config'")
        all_ok = False

    # Custom client ID
    if check_custom_client_id():
        print(f"{Fore.GREEN}  ✓ Custom OAuth Client ID detected (fast mode)")
    else:
        print(f"{Fore.YELLOW}  ⚠ Using SHARED client ID (5-10x slower)")
        print(f"    → Run 'cloudboost setup' to fix this")

    # Compressor
    compressor = get_compressor_name()
    if compressor == "zstd":
        print(f"{Fore.GREEN}  ✓ zstd compression available (5-10x faster than gzip)")
    else:
        print(f"{Fore.YELLOW}  ⚠ zstd not installed — using gzip fallback")
        print(f"    → pip install zstandard")

    # Config
    user_config = load_config()
    if user_config:
        print(f"{Fore.GREEN}  ✓ Config file loaded")
    else:
        print(f"{Fore.YELLOW}  ⚠ Using defaults — customize at ~/.cloudboost/config.yml")

    print()
    if all_ok:
        print(f"{Fore.GREEN}  All critical checks passed!\n")
    else:
        print(f"{Fore.RED}  Some issues found — fix them above.\n")


@main.command()
def setup():
    """Interactive setup guide for maximum upload speed."""
    print(BANNER)
    print_setup_guide()


@main.command()
def stats():
    """Show rclone config and cloud connection stats."""
    print(BANNER)

    if not check_rclone_installed():
        print(f"{Fore.RED}  ✗ rclone not installed\n")
        return

    remotes = get_rclone_remotes()
    print(f"{Fore.CYAN}→ Configured remotes:\n")
    if remotes:
        for r in remotes:
            print(f"  ✓ {r}")
    else:
        print(f"  No remotes — run 'rclone config'")

    print(f"\n{Fore.CYAN}→ Compressor: {get_compressor_name()}\n")


@main.command(name="init")
def init_cmd():
    """Initialize cloud-boost config in ~/.cloudboost/"""
    print(BANNER)
    ensure_dirs()
    save_default_config()
    print(f"{Fore.GREEN}  ✓ Config initialized at ~/.cloudboost/\n")


main.add_command(upload, name="upload")

if __name__ == "__main__":
    main()
