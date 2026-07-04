#!/usr/bin/env python3
"""
Comprehensive Disaster Recovery Script
Handles complete system recovery from Backblaze B2 backups
"""

import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from restore_from_b2 import init_b2, ensure_bucket, restore_prefix_to_local, get_env

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("disaster_recovery.log"),
        logging.StreamHandler()
    ]
)

def create_recovery_report(recovery_path: Path, files_restored: int, start_time: datetime) -> None:
    """Create a detailed recovery report"""
    end_time = datetime.utcnow()
    duration = (end_time - start_time).total_seconds()
    
    report = {
        "recovery_timestamp": end_time.isoformat() + "Z",
        "recovery_duration_seconds": duration,
        "files_restored": files_restored,
        "recovery_path": str(recovery_path),
        "status": "completed",
        "start_time": start_time.isoformat() + "Z",
        "end_time": end_time.isoformat() + "Z"
    }
    
    report_path = Path("recovery_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    logging.info(f"Recovery report saved to {report_path}")
    
    # Print summary
    print(f"\n{'='*50}")
    print("🔄 DISASTER RECOVERY COMPLETE")
    print(f"{'='*50}")
    print(f"Files restored: {files_restored}")
    print(f"Recovery time: {duration:.1f} seconds")
    print(f"Recovery path: {recovery_path}")
    print(f"Report saved: {report_path}")
    print(f"{'='*50}\n")

def verify_recovery(recovery_path: Path) -> bool:
    """Verify that recovery was successful"""
    logging.info("Verifying recovery...")
    
    # Check if essential files exist
    essential_files = [
        "index.html",
        "data/content.json",
        "css/styles.css",
        "js/main.js"
    ]
    
    missing_files = []
    for file_path in essential_files:
        full_path = recovery_path / file_path
        if not full_path.exists():
            missing_files.append(file_path)
    
    if missing_files:
        logging.error(f"Recovery verification failed. Missing files: {missing_files}")
        return False
    
    # Check content.json is valid JSON
    try:
        content_path = recovery_path / "data" / "content.json"
        with open(content_path, "r") as f:
            json.load(f)
        logging.info("content.json is valid JSON")
    except Exception as e:
        logging.error(f"content.json validation failed: {e}")
        return False
    
    logging.info("✅ Recovery verification passed")
    return True

def backup_existing_site(site_dir: Path) -> Optional[Path]:
    """Backup existing site before recovery"""
    if not site_dir.exists():
        logging.info("No existing site to backup")
        return None
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = site_dir.parent / f"site_backup_{timestamp}"
    
    try:
        logging.info(f"Backing up existing site to {backup_path}")
        shutil.copytree(site_dir, backup_path)
        logging.info(f"✅ Existing site backed up to {backup_path}")
        return backup_path
    except Exception as e:
        logging.error(f"Failed to backup existing site: {e}")
        return None

def main():
    """Main disaster recovery function"""
    print("🚨 DISASTER RECOVERY SYSTEM")
    print("=" * 50)
    
    start_time = datetime.utcnow()
    load_dotenv()
    
    # Get configuration
    bucket_name = get_env("B2_BUCKET_NAME", required=True)
    prefix = get_env("B2_PREFIX", default="docs")
    site_dir = Path(get_env("SITE_DIR", default="docs"))
    
    print(f"Bucket: {bucket_name}")
    print(f"Prefix: {prefix}")
    print(f"Recovery path: {site_dir}")
    print()
    
    # Confirm recovery
    if not input("⚠️  This will replace the current site. Continue? (y/N): ").lower().startswith('y'):
        print("Recovery cancelled.")
        return
    
    try:
        # Step 1: Backup existing site
        print("\n📦 Step 1: Backing up existing site...")
        backup_path = backup_existing_site(site_dir)
        
        # Step 2: Initialize B2 connection
        print("\n🔗 Step 2: Connecting to Backblaze B2...")
        b2_api = init_b2()
        bucket = ensure_bucket(b2_api, bucket_name)
        logging.info(f"Connected to bucket: {bucket_name}")
        
        # Step 3: Remove existing site
        if site_dir.exists():
            print(f"\n🗑️  Step 3: Removing existing site at {site_dir}...")
            shutil.rmtree(site_dir)
            logging.info(f"Removed existing site directory: {site_dir}")
        
        # Step 4: Restore from B2
        print(f"\n⬇️  Step 4: Restoring from B2 (prefix: {prefix})...")
        files_restored = restore_prefix_to_local(bucket, prefix, site_dir)
        
        if files_restored == 0:
            raise RuntimeError("No files were restored from backup")
        
        # Step 5: Verify recovery
        print("\n✅ Step 5: Verifying recovery...")
        if not verify_recovery(site_dir):
            raise RuntimeError("Recovery verification failed")
        
        # Step 6: Create recovery report
        print("\n📊 Step 6: Creating recovery report...")
        create_recovery_report(site_dir, files_restored, start_time)
        
        # Step 7: Optional git operations
        git_remote = get_env("GIT_REMOTE")
        git_branch = get_env("GIT_BRANCH")
        
        if git_remote and git_branch:
            print(f"\n🔄 Step 7: Deploying to {git_remote}/{git_branch}...")
            try:
                subprocess.run(["git", "add", str(site_dir)], check=True, capture_output=True)
                commit_message = f"Disaster recovery completed on {datetime.utcnow().isoformat()}Z"
                subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True)
                subprocess.run(["git", "push", git_remote, git_branch], check=True, capture_output=True)
                logging.info("✅ Successfully deployed to git repository")
            except subprocess.CalledProcessError as e:
                logging.warning(f"Git deployment failed: {e}")
                print("⚠️  Git deployment failed - manual push may be required")
        
        print("\n🎉 DISASTER RECOVERY SUCCESSFUL!")
        print(f"✅ {files_restored} files restored")
        print(f"✅ Site recovered to: {site_dir}")
        if backup_path:
            print(f"✅ Previous site backed up to: {backup_path}")
        
    except Exception as e:
        logging.exception("Disaster recovery failed")
        print(f"\n❌ DISASTER RECOVERY FAILED: {e}")
        
        # If we have a backup, offer to restore it
        if 'backup_path' in locals() and backup_path and backup_path.exists():
            if input(f"\n🔄 Restore previous site from {backup_path}? (y/N): ").lower().startswith('y'):
                try:
                    if site_dir.exists():
                        shutil.rmtree(site_dir)
                    shutil.copytree(backup_path, site_dir)
                    print(f"✅ Previous site restored from {backup_path}")
                except Exception as restore_error:
                    print(f"❌ Failed to restore previous site: {restore_error}")
        
        sys.exit(1)

if __name__ == "__main__":
    main()
