import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv
from b2sdk.v2 import InMemoryAccountInfo, B2Api


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_env() -> None:
    load_dotenv(override=False)


def get_env(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        logging.error("Missing required environment variable: %s", name)
        sys.exit(2)
    return value


def init_b2() -> B2Api:
    app_key_id = get_env("B2_APPLICATION_KEY_ID", required=True)
    app_key = get_env("B2_APPLICATION_KEY", required=True)
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    logging.info("Authorizing against Backblaze B2...")
    b2_api.authorize_account("production", app_key_id, app_key)
    logging.info("Authorization successful.")
    return b2_api


def ensure_bucket(b2_api: B2Api, bucket_name: str):
    bucket = b2_api.get_bucket_by_name(bucket_name)
    if bucket is None:
        raise RuntimeError(f"Bucket '{bucket_name}' not found. Create it in Backblaze first.")
    return bucket


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _iter_bucket_files(bucket, prefix: str):
    """
    Yield file version-like objects for all files under the given prefix.
    Works with both old and new b2sdk versions.
    """
    folder_name = (prefix or "").rstrip("/")
    iterator = None
    attempts = [
        (folder_name,),                 # ls(folder_name)
        (folder_name, False),           # ls(folder_name, show_versions=False) OR (folder_name, latest_only=False)
        (folder_name, None, False),     # ls(folder_name, fetch_count=None, show_versions=False)
    ]
    last_err = None
    for args in attempts:
        try:
            iterator = bucket.ls(*args)
            break
        except TypeError as e:
            last_err = e
            continue
    if iterator is None:
        # Fallback to listing root if everything else fails
        try:
            iterator = bucket.ls("")
        except Exception:
            # Re-raise the last TypeError for clarity
            if last_err:
                raise last_err
            raise

    for entry in iterator:
        # Some versions return (file_version, folder)
        if isinstance(entry, tuple):
            file_version = entry[0]
        else:
            file_version = entry

        # Skip folder entries
        if getattr(file_version, "file_name", "").endswith("/"):
            continue

        # Apply prefix filter if needed
        if prefix:
            normalized = prefix.rstrip("/")
            if not (
                file_version.file_name == normalized
                or file_version.file_name.startswith(normalized + "/")
            ):
                continue

        yield file_version



def restore_prefix_to_local(bucket, prefix: str, site_dir: Path) -> int:
    """
    Downloads all files under prefix to the local site_dir.
    Returns number of files restored.
    """
    ensure_directory(site_dir)
    count = 0
    logging.info("Listing files in bucket '%s' under prefix '%s/'", bucket.name, prefix)
    for file_version in _iter_bucket_files(bucket, prefix):
        relative = file_version.file_name[len(prefix):].lstrip("/")
        local_path = site_dir / relative
        ensure_directory(local_path.parent)
        logging.info("Downloading b2://%s/%s -> %s", bucket.name, file_version.file_name, local_path)
        downloader = bucket.download_file_by_name(file_version.file_name)
        with open(local_path, "wb") as f:
            downloader.save(f)
        count += 1
    if count == 0:
        logging.warning("No files found for prefix '%s'.", prefix)
    else:
        logging.info("Restore complete. %d files downloaded.", count)
    return count


def run_git_commands(site_dir: Path, remote: str, branch: str) -> Tuple[int, str]:
    """
    Adds the site_dir, commits changes, and pushes to the given remote/branch.
    Returns (exit_code, message).
    """
    try:
        subprocess.run(["git", "add", str(site_dir)], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stderr.decode(errors="ignore")

    commit_message = f"Restore site from Backblaze B2 on {datetime.utcnow().isoformat()}Z"
    commit_proc = subprocess.run(["git", "commit", "-m", commit_message], capture_output=True)
    if commit_proc.returncode != 0:
        # Possibly "nothing to commit" – proceed to push anyway
        logging.info(commit_proc.stderr.decode(errors="ignore").strip() or "No changes to commit.")

    push_proc = subprocess.run(["git", "push", remote, branch], capture_output=True)
    if push_proc.returncode != 0:
        return push_proc.returncode, push_proc.stderr.decode(errors="ignore")
    return 0, push_proc.stdout.decode(errors="ignore")


def main() -> None:
    configure_logging()
    load_env()

    bucket_name = get_env("B2_BUCKET_NAME", required=True)
    prefix = get_env("B2_PREFIX", default="docs")
    site_dir = Path(get_env("SITE_DIR", default="docs"))
    git_remote = get_env("GIT_REMOTE", default="origin")
    git_branch = get_env("GIT_BRANCH", default="main")

    try:
        b2_api = init_b2()
        bucket = ensure_bucket(b2_api, bucket_name)
        restored = restore_prefix_to_local(bucket, prefix, site_dir)
        if restored > 0:
            logging.info("Redeploying to GitHub Pages via git push...")
            code, msg = run_git_commands(site_dir, git_remote, git_branch)
            if code != 0:
                logging.error("Git push failed: %s", msg)
                sys.exit(code or 1)
            logging.info("Git push successful. Your GitHub Pages site should update shortly.")
        else:
            logging.warning("Nothing restored; skipping git deploy.")
    except SystemExit:
        raise
    except Exception as exc:
        logging.exception("Restore failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()


