"""
cloud-boost CLI
Usage: cloudboost upload <file> <destination> [options]
"""

import click
from colorama import Fore, Style, init
from cloudboost.uploader import upload

init(autoreset=True)

BANNER = f"""
{Fore.CYAN}
  ██████╗██╗      ██████╗ ██╗   ██╗██████╗        
 ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ██║     ██║     ██║   ██║██║   ██║██║  ██║       
 ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝       
  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝  BOOST
{Fore.WHITE}  ⚡ Supercharge your cloud uploads
{Style.RESET_ALL}"""


@click.group()
def main():
    """⚡ cloud-boost — fast parallel cloud uploads"""
    pass


@main.command()
@click.argument("filepath")
@click.argument("destination")
@click.option("--threads", "-t", default=6, help="Number of parallel upload threads (default: 6)")
@click.option("--chunk-size", "-c", default=5, help="Chunk size in MB (default: 5)")
@click.option("--no-compress", is_flag=True, default=False, help="Disable compression")
def upload_cmd(filepath, destination, threads, chunk_size, no_compress):
    """Upload FILE to DESTINATION with parallel chunked upload.

    \b
    Examples:
      cloudboost upload myfile.zip gdrive:/backup/
      cloudboost upload ./project gdrive:/projects/ --threads 8
      cloudboost upload bigfile.iso gdrive:/iso/ --chunk-size 10
    """
    print(BANNER)
    upload(
        filepath=filepath,
        destination=destination,
        threads=threads,
        chunk_size_mb=chunk_size,
        compress=not no_compress,
    )


@main.command()
@click.argument("filepath")
@click.argument("destination")
def resume(filepath, destination):
    """Resume an interrupted upload.

    \b
    Example:
      cloudboost resume myfile.zip gdrive:/backup/
    """
    print(BANNER)
    print(f"{Fore.MAGENTA}→ Resuming upload: {filepath}\n")
    upload(filepath=filepath, destination=destination)


@main.command()
def stats():
    """Show rclone config and cloud connection stats."""
    import subprocess
    print(BANNER)
    print(f"{Fore.CYAN}→ rclone remotes configured:\n")
    result = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True)
    if result.returncode == 0:
        remotes = result.stdout.strip().split("\n")
        for r in remotes:
            print(f"  ✓ {r}")
    else:
        print(f"{Fore.RED}  ✗ rclone not found or not configured.")
    print()


# Alias: cloudboost upload → main entry
main.add_command(upload_cmd, name="upload")

if __name__ == "__main__":
    main()
