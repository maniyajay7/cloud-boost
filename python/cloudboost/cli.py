"""
cloud-boost CLI v2
Production CLI with auto-tuning, folder support, diagnostics
"""

import sys
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
from cloudboost.transport import check_rclone_installed, get_rclone_remotes
from cloudboost.logger import get_logger

init(autoreset=True)

BANNER = f"""{Fore.CYAN}
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST
{Fore.WHITE}  ⚡ v2.0 — Production Upload Engine
{Style.RESET_ALL}"""


@click.group()
@click.version_option(version="2.0.0")
def main():
    """⚡ cloud-boost — supercharge your cloud uploads"""
    ensure_dirs()
    save_default_config()


@main.command()
@click.argument("source")
@click.argument("destination")
@click.option("--threads", "-t", default=None, type=int, help="Upload threads (auto-tuned by default)")
@click.option("--chunk-size", "-c", default=None, help="Chunk size e.g. 8MB, 32MB (auto-tuned)")
@click.option("--no-compress", is_flag=True, default=False, help="Disable compression")
@click.option("--compress-level", default=None, type=int, help="Gzip level 1-9 (auto-tuned)")
@click.option("--no-resume", is_flag=True, default=False, help="Disable resume/checkpoint")
@click.option("--no-dedup", is_flag=True, default=False, help="Disable deduplication")
@click.option("--no-checksum", is_flag=True, default=False, help="Disable SHA-256 verification")
@click.option("--max-bw", default=None, help="Bandwidth limit e.g. 50MB, 100MB")
@click.option("--provider", "-p", default="gdrive", help="Cloud provider (gdrive, s3, azure, dropbox)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose output")
def upload(
    source, destination, threads, chunk_size, no_compress, compress_level,
    no_resume, no_dedup, no_checksum, max_bw, provider, verbose,
):
    """Upload FILE or FOLDER to cloud DESTINATION.

    \b
    Examples:
      cloudboost upload myfile.zip gdrive:/backup/
      cloudboost upload ./project gdrive:/projects/ --threads 8
      cloudboost upload big.iso gdrive:/iso/ --chunk-size 32MB
      cloudboost upload data/ gdrive:/data/ --max-bw 50MB
    """
    print(BANNER)

    # Build config from CLI args (None values → auto-tuned)
    overrides = {
        "provider": provider,
        "verbose": verbose,
        "compression": not no_compress,
        "resume": not no_resume,
        "dedup": not no_dedup,
        "checksum_verify": not no_checksum,
    }
    if threads is not None:
        overrides["threads"] = threads
    if chunk_size is not None:
        overrides["chunk_size"] = parse_size(chunk_size)
    if compress_level is not None:
        overrides["compression_level"] = compress_level
    if max_bw is not None:
        overrides["max_bandwidth"] = parse_size(max_bw)

    config = UploadConfig(**{k: v for k, v in overrides.items() if v is not None})

    import os
    if os.path.isdir(source):
        success = upload_folder(source, destination, config)
    else:
        success = upload_file(source, destination, config)

    sys.exit(0 if success else 1)


@main.command()
@click.argument("source")
@click.argument("destination")
@click.option("--verbose", "-v", is_flag=True, default=False)
def resume(source, destination, verbose):
    """Resume an interrupted upload.

    \b
    Example:
      cloudboost resume bigfile.zip gdrive:/backup/
    """
    print(BANNER)
    print(f"{Fore.MAGENTA}→ Resuming upload: {source}\n")

    config = UploadConfig(resume=True, verbose=verbose)
    success = upload_file(source, destination, config)
    sys.exit(0 if success else 1)


@main.command()
def doctor():
    """Diagnose cloud-boost setup — check rclone, remotes, config."""
    print(BANNER)
    print(f"{Fore.CYAN}→ Running diagnostics...\n")

    # Check rclone
    if check_rclone_installed():
        print(f"{Fore.GREEN}  ✓ rclone installed")
    else:
        print(f"{Fore.RED}  ✗ rclone NOT found — install from https://rclone.org/install/")
        return

    # Check remotes
    remotes = get_rclone_remotes()
    if remotes:
        print(f"{Fore.GREEN}  ✓ {len(remotes)} remote(s) configured:")
        for r in remotes:
            print(f"    · {r}")
    else:
        print(f"{Fore.YELLOW}  ⚠ No remotes configured — run 'rclone config'")

    # Check config
    user_config = load_config()
    if user_config:
        print(f"{Fore.GREEN}  ✓ Config file loaded")
    else:
        print(f"{Fore.YELLOW}  ⚠ Using default config — customize at ~/.cloudboost/config.yml")

    print(f"\n{Fore.GREEN}  All checks passed!\n")


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
        print(f"  No remotes configured — run 'rclone config'")

    # Show current config
    print(f"\n{Fore.CYAN}→ Current config:\n")
    config = load_config()
    for key, val in config.items():
        print(f"  {key}: {val}")
    print()


@main.command()
def init():
    """Initialize cloud-boost config in ~/.cloudboost/"""
    print(BANNER)
    ensure_dirs()
    save_default_config()
    print(f"{Fore.GREEN}  ✓ Config initialized at ~/.cloudboost/config.yml")
    print(f"{Fore.WHITE}  Edit it to customize your upload settings.\n")


main.add_command(upload, name="upload")

if __name__ == "__main__":
    main()
