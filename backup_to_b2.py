import hashlib
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from b2sdk.v2 import InMemoryAccountInfo, B2Api, UploadSourceLocalFile


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def load_env() -> None:
    # Load variables from .env into environment
    load_dotenv(override=False)


def get_env(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        logging.error("Missing required environment variable: %s", name)
        sys.exit(2)
    return value


def init_b2(app_key_id: Optional[str] = None, app_key: Optional[str] = None) -> B2Api:
    app_key_id = app_key_id or os.getenv("B2_APPLICATION_KEY_ID")
    app_key = app_key or os.getenv("B2_APPLICATION_KEY")
    if not app_key_id:
        raise ValueError("B2_APPLICATION_KEY_ID is not set.")
    if not app_key:
        raise ValueError("B2_APPLICATION_KEY is not set.")
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)
    logging.info("Authorizing against Backblaze B2...")
    b2_api.authorize_account("production", app_key_id, app_key)
    logging.info("Authorization successful.")
    return b2_api


def ensure_bucket(b2_api: B2Api, bucket_name: str):
    try:
        bucket = b2_api.get_bucket_by_name(bucket_name)
        if bucket is None:
            raise RuntimeError(f"Bucket '{bucket_name}' not found. Create it in Backblaze first.")
        return bucket
    except Exception as exc:
        logging.exception("Failed to get bucket '%s': %s", bucket_name, exc)
        raise


def guess_content_type(path: Path) -> Optional[str]:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

def upload_file_with_retry(bucket, local_path: Path, b2_name: str, content_type: str, max_retries: int = 3) -> bool:
    """Upload a single file with retry logic"""
    for attempt in range(max_retries):
        try:
            logging.info("Uploading %s -> b2://%s/%s (attempt %d/%d)", local_path, bucket.name, b2_name, attempt + 1, max_retries)
            
            # Calculate file hash for integrity verification
            file_hash = calculate_file_hash(local_path)
            
            file_info = {
                'sha256': file_hash,
                'upload_timestamp': str(int(time.time()))
            }
            
            bucket.upload(
                UploadSourceLocalFile(local_path),
                file_name=b2_name,
                content_type=content_type,
                file_info=file_info
            )
            logging.info("Successfully uploaded %s (SHA256: %s)", local_path, file_hash[:8])
            return True
        except Exception as exc:
            logging.warning("Upload attempt %d failed for %s: %s", attempt + 1, local_path, exc)
            if attempt == max_retries - 1:
                logging.error("All upload attempts failed for %s", local_path)
                raise
            time.sleep(2 ** attempt)  # Exponential backoff
    return False

def upload_directory_to_b2(site_dir: Path, bucket, prefix: str) -> int:
    """Upload directory to B2 with retry logic and integrity checks"""
    total_files = 0
    failed_files = []
    
    for root, _, files in os.walk(site_dir):
        for filename in files:
            local_path = Path(root) / filename
            rel_path = local_path.relative_to(site_dir).as_posix()
            b2_name = f"{prefix}/{rel_path}".replace("\\", "/")
            content_type = guess_content_type(local_path)
            
            try:
                if upload_file_with_retry(bucket, local_path, b2_name, content_type):
                    total_files += 1
                else:
                    failed_files.append(str(local_path))
            except Exception as exc:
                logging.error("Failed to upload %s: %s", local_path, exc)
                failed_files.append(str(local_path))
    
    if failed_files:
        logging.error("Failed to upload %d files: %s", len(failed_files), failed_files)
        raise RuntimeError(f"Upload incomplete. {len(failed_files)} files failed.")
    
    if total_files == 0:
        logging.warning("No files found in %s to upload.", site_dir)
    else:
        logging.info("Upload complete. %d files uploaded successfully.", total_files)
    
    return total_files


def backup_site_to_b2(*, load_env_vars: bool = True) -> int:
    """
    Perform the site backup to Backblaze B2.

    Returns the number of files uploaded.
    """
    if load_env_vars:
        load_env()

    site_dir = Path(os.getenv("SITE_DIR", "docs"))
    if not site_dir.exists() or not site_dir.is_dir():
        raise FileNotFoundError(f"Site directory '{site_dir}' does not exist.")

    bucket_name = os.getenv("B2_BUCKET_NAME")
    if not bucket_name:
        raise ValueError("B2_BUCKET_NAME is not set.")

    prefix = os.getenv("B2_PREFIX", "docs")

    b2_api = init_b2()
    bucket = ensure_bucket(b2_api, bucket_name)
    return upload_directory_to_b2(site_dir, bucket, prefix)


def main() -> None:
    configure_logging()
    try:
        files_uploaded = backup_site_to_b2(load_env_vars=True)
        logging.info("Backup finished. %d files uploaded.", files_uploaded)
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        sys.exit(2)
    except ValueError as exc:
        logging.error("%s", exc)
        sys.exit(2)
    except Exception as exc:
        logging.exception("Backup failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

