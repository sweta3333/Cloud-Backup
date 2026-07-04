#!/usr/bin/env python3
"""
Backup monitoring and alerting system
Checks backup health and sends notifications if issues are detected
"""

import json
import logging
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log"),
        logging.StreamHandler()
    ]
)

def load_metrics() -> Dict:
    """Load backup metrics from metrics.json"""
    metrics_path = Path("metrics.json")
    if not metrics_path.exists():
        return {}
    
    try:
        with open(metrics_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load metrics: {e}")
        return {}

def check_backup_health(metrics: Dict) -> Dict:
    """Check backup health and return status"""
    now = datetime.utcnow()
    issues = []
    
    # Check last backup time
    last_backup = metrics.get("last_backup")
    if last_backup:
        try:
            last_backup_time = datetime.fromisoformat(last_backup.replace("Z", "+00:00"))
            hours_since_backup = (now - last_backup_time.replace(tzinfo=None)).total_seconds() / 3600
            
            if hours_since_backup > 48:  # No backup in 48 hours
                issues.append(f"No backup in {hours_since_backup:.1f} hours")
        except Exception as e:
            issues.append(f"Invalid backup timestamp: {last_backup}")
    else:
        issues.append("No backup timestamp found")
    
    # Check backup status
    backup_status = metrics.get("backup_status")
    if backup_status == "error":
        error_msg = metrics.get("last_backup_error", "Unknown error")
        issues.append(f"Last backup failed: {error_msg}")
    
    # Check file count
    total_files = metrics.get("total_files", 0)
    if total_files == 0:
        issues.append("No files in backup")
    
    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "last_backup": last_backup,
        "total_files": total_files,
        "backup_status": backup_status
    }

def send_email_alert(subject: str, body: str) -> bool:
    """Send email alert if configured"""
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    alert_email = os.getenv("ALERT_EMAIL")
    
    if not all([smtp_server, smtp_username, smtp_password, alert_email]):
        logging.warning("Email not configured - skipping alert")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_username
        msg['To'] = alert_email
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        
        logging.info(f"Alert email sent to {alert_email}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email alert: {e}")
        return False

def send_windows_notification(title: str, message: str) -> bool:
    """Send Windows toast notification"""
    try:
        import win10toast
        toaster = win10toast.ToastNotifier()
        toaster.show_toast(title, message, duration=10)
        return True
    except ImportError:
        logging.warning("win10toast not installed - install with: pip install win10toast")
        return False
    except Exception as e:
        logging.error(f"Failed to send Windows notification: {e}")
        return False

def main():
    """Main monitoring function"""
    load_dotenv()
    
    logging.info("Starting backup health check...")
    
    metrics = load_metrics()
    health = check_backup_health(metrics)
    
    if health["healthy"]:
        logging.info("✅ Backup system is healthy")
        print("✅ All backup checks passed")
        
        # Log summary
        if health["last_backup"]:
            last_backup_time = datetime.fromisoformat(health["last_backup"].replace("Z", "+00:00"))
            logging.info(f"Last backup: {last_backup_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        logging.info(f"Total files: {health['total_files']}")
        
    else:
        logging.warning("❌ Backup system has issues")
        print("❌ Backup health check failed")
        
        for issue in health["issues"]:
            logging.warning(f"  - {issue}")
            print(f"  - {issue}")
        
        # Send alerts
        alert_subject = "🚨 Backup System Alert"
        alert_body = f"""Backup health check failed at {datetime.utcnow().isoformat()}Z

Issues detected:
{chr(10).join(f'- {issue}' for issue in health['issues'])}

Last backup: {health['last_backup'] or 'Never'}
Total files: {health['total_files']}
Backup status: {health['backup_status']}

Please check the backup system immediately.
"""
        
        # Try to send email alert
        send_email_alert(alert_subject, alert_body)
        
        # Try to send Windows notification
        send_windows_notification(
            "Backup System Alert", 
            f"{len(health['issues'])} issues detected. Check logs for details."
        )
        
        sys.exit(1)

if __name__ == "__main__":
    main()
