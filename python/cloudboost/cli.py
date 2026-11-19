"""
cloud-boost v3 CLI
Production CLI with entropy-aware compression, quota tracking, setup wizard.
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
    MB,
)
from cloudboost.uploader import upload_file, upload_folder
from cloudboost.transport import (
    check_rclone_installed,
    get_rclone_remotes,
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


@click.group()
@click.version_option(version="3.0.0")
def main():
    """⚡ cloud-boost — supercharge your cloud uploads"""
    ensure_dirs()
    save_default_config()


@main.command()
@click.argument("source")
@click.argument("destination")
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
    Examples:
      cloudboost upload myfile.zip gdrive:/backup/
      cloudboost upload ./game-folder gdrive:/games/ --provider gdrive
      cloudboost upload data.tar gdrive:/data/ --max-bw 50MB
      cloudboost upload huge.iso gdrive:/iso/ --max-transfer 700GB
    """
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
