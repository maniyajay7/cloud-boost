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
    """Full interactive upload wizard — runs when 'cloudboost upload' has no args."""
    print(BANNER)
    print(f"  {Fore.GREEN}⚡ Interactive Upload Wizard{Style.RESET_ALL}")
    print(f"  {Fore.LIGHTBLACK_EX}Follow the prompts to upload your files.{Style.RESET_ALL}\n")

    # ─── Step 1: Choose cloud provider ────────────────────────────────────────
    remotes = get_rclone_remotes()
    provider_options = []

    # Dynamically build from configured remotes
    provider_map = {
        "gdrive": ("Google Drive", "Fast uploads with custom OAuth"),
        "s3": ("Amazon S3", "AWS S3 compatible storage"),
        "azure": ("Azure Blob", "Microsoft Azure storage"),
        "dropbox": ("Dropbox", "Dropbox cloud storage"),
        "spaces": ("DigitalOcean Spaces", "S3-compatible object storage"),
        "supabase": ("Supabase Storage", "Postgres-backed storage"),
    }

    # Show configured remotes first
    seen_providers = set()
    for remote in remotes:
        remote_name = remote.rstrip(":")
        # Try to match remote to a known provider
        for prov_key, (prov_label, prov_desc) in provider_map.items():
            if prov_key in remote_name.lower():
                provider_options.append({
                    "label": f"{prov_label} ({remote})",
                    "value": prov_key,
                    "remote": remote,
                    "desc": f"✓ Configured — {prov_desc}",
                })
                seen_providers.add(prov_key)
                break
        else:
            # Unknown remote type — still show it
            provider_options.append({
                "label": f"{remote_name} ({remote})",
                "value": "gdrive",
                "remote": remote,
                "desc": "✓ Configured remote",
            })

    # Add unconfigured providers
    for prov_key, (prov_label, prov_desc) in provider_map.items():
        if prov_key not in seen_providers:
            provider_options.append({
                "label": prov_label,
                "value": prov_key,
                "remote": f"{prov_key}:",
                "desc": f"Not configured — run 'rclone config'",
            })

    chosen_provider = _prompt_choice("Select Cloud Provider", provider_options)
    provider = chosen_provider["value"]
    remote = chosen_provider["remote"]

    # ─── Step 2: Source file/folder ───────────────────────────────────────────
    print()
    while True:
        source = _prompt_input("Enter file or folder path")
        if not source:
            print(f"  {Fore.RED}  Path cannot be empty{Style.RESET_ALL}")
            continue
        # Strip surrounding quotes
        source = source.strip('"').strip("'")
        if os.path.exists(source):
            if os.path.isdir(source):
                file_count = sum(1 for f in os.scandir(source) if f.is_file())
                print(f"  {Fore.GREEN}  ✓ Folder found — {file_count} file(s){Style.RESET_ALL}")
            else:
                size = os.path.getsize(source)
                print(f"  {Fore.GREEN}  ✓ File found — {format_size(size)}{Style.RESET_ALL}")
            break
        else:
            print(f"  {Fore.RED}  ✗ Path not found: {source}{Style.RESET_ALL}")
            print(f"  {Fore.YELLOW}    Tip: Drag & drop the file into the terminal, or paste the full path{Style.RESET_ALL}")

    # ─── Step 3: Destination path ─────────────────────────────────────────────
    print()
    default_dest = f"{remote}/Backup/"
    destination = _prompt_input("Enter destination path on cloud", default_dest)
    # Ensure remote prefix
    if ":" not in destination:
        destination = f"{remote}{destination}"

    # ─── Step 4: Compression preference ───────────────────────────────────────
    compress_options = [
        {"label": "Smart (auto-detect)", "value": "smart", "desc": "Compress only if beneficial"},
        {"label": "No compression", "value": "none", "desc": "Upload file as-is (original format)"},
        {"label": "Force compress", "value": "force", "desc": f"Always compress with {get_compressor_name()}"},
    ]
    chosen_compress = _prompt_choice("Compression Mode", compress_options)

    # ─── Step 5: Advanced options ─────────────────────────────────────────────
    threads = None
    max_bw = None
    verbose = False

    show_advanced = _prompt_yes_no("Configure advanced options?", default=False)
    if show_advanced:
        print()
        raw_threads = _prompt_input("Upload threads", "auto")
        if raw_threads != "auto":
            try:
                threads = int(raw_threads)
            except ValueError:
                pass

        raw_bw = _prompt_input("Bandwidth limit (e.g. 50MB, or 'none')", "none")
        if raw_bw.lower() != "none":
            try:
                max_bw = parse_size(raw_bw)
            except Exception:
                pass

        verbose = _prompt_yes_no("Enable verbose output?", default=False)

    # ─── Confirmation ─────────────────────────────────────────────────────────
    print(f"\n  {Fore.CYAN}{'═' * 50}")
    print(f"  {Fore.WHITE}  Upload Summary")
    print(f"  {Fore.CYAN}{'═' * 50}")
    print(f"  {Fore.WHITE}  Source      : {source}")
    print(f"  {Fore.WHITE}  Destination : {destination}")
    print(f"  {Fore.WHITE}  Provider    : {provider}")
    print(f"  {Fore.WHITE}  Compression : {chosen_compress['label']}")
    if threads:
        print(f"  {Fore.WHITE}  Threads     : {threads}")
    if max_bw:
        print(f"  {Fore.WHITE}  Bandwidth   : {format_size(max_bw)}/s")
    print(f"  {Fore.CYAN}{'═' * 50}")

    if not _prompt_yes_no("Start upload?", default=True):
        print(f"\n  {Fore.YELLOW}Upload cancelled.{Style.RESET_ALL}\n")
        sys.exit(0)

    # ─── Build config and run ─────────────────────────────────────────────────
    no_compress = chosen_compress["value"] == "none"

    overrides = {
        "provider": provider,
        "verbose": verbose,
        "compression": not no_compress,
        "dedup": True,
    }
    if threads is not None:
        overrides["rclone_transfers"] = threads
    if max_bw is not None and max_bw > 0:
        overrides["max_bandwidth"] = max_bw

    config = UploadConfig(**{k: v for k, v in overrides.items() if v is not None})
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
