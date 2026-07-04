import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory,render_template

from backup_to_b2 import backup_site_to_b2


ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "docs"
CONTENT_PATH = SITE_DIR / "data" / "content.json"
METRICS_PATH = ROOT / "metrics.json"
BACKUP_DIR = ROOT / "backup_temp"

load_dotenv(override=False)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "backup.log"),
        logging.StreamHandler()
    ]
)

app = Flask(__name__)

# Authentication decorator
def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_token = os.getenv("ADMIN_TOKEN")
        if not auth_token:
            logging.warning("No ADMIN_TOKEN set - authentication disabled")
            return f(*args, **kwargs)
        
        # Check Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        
        token = auth_header.split(" ", 1)[1]
        if token != auth_token:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# Metrics tracking
def update_metrics(operation, status="success", files_count=0, error_msg=None):
    """Update backup/restore metrics"""
    try:
        metrics = {}
        if METRICS_PATH.exists():
            with open(METRICS_PATH, "r") as f:
                metrics = json.load(f)
        
        timestamp = datetime.utcnow().isoformat() + "Z"
        metrics[f"last_{operation}"] = timestamp
        metrics[f"{operation}_status"] = status
        if files_count > 0:
            metrics["total_files"] = files_count
        if error_msg:
            metrics[f"last_{operation}_error"] = error_msg
        
        with open(METRICS_PATH, "w") as f:
            json.dump(metrics, f, indent=2)
        
        logging.info(f"Updated metrics: {operation} {status}, files: {files_count}")
    except Exception as e:
        logging.error(f"Failed to update metrics: {e}")


def run_script(script_name: str):
    """Execute a Python script and return (ok, message)."""
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / script_name)],
            capture_output=True,
            text=True,
            check=True,
        )
        output = (
            completed.stdout.strip()
            or completed.stderr.strip()
            or "Operation completed."
        )
        return True, output
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() or exc.stdout.strip() or "Unknown error."
        return False, err



@app.route("/admin/logs")
def admin_logs():
    """Get recent log entries"""
    try:
        log_path = ROOT / "backup.log"
        if not log_path.exists():
            return jsonify({"logs": ["No logs available"]})
        
        with open(log_path, "r") as f:
            lines = f.readlines()
            # Get last 20 lines
            recent_logs = lines[-20:] if len(lines) > 20 else lines
            return jsonify({"logs": [line.strip() for line in recent_logs]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# ROUTES: User Website (served from /docs)
# ============================================================================

@app.route("/")
def root():
    """Serve the main user website or redirect to admin if docs doesn't exist"""
    if not SITE_DIR.exists() or not (SITE_DIR / "index.html").exists():
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta http-equiv="refresh" content="0; url=/admin">
            <title>Redirecting...</title>
        </head>
        <body>
            <p>Website not available. <a href="/admin">Go to Admin Panel</a></p>
        </body>
        </html>
        '''
    return send_from_directory(str(SITE_DIR), "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    """Serve static files from /docs directory"""
    # Skip admin routes - they are handled above
    if filename.startswith('admin'):
        return jsonify({"error": "Not found"}), 404
    
    if not SITE_DIR.exists():
        return jsonify({"error": "Site files not available. Visit /admin to restore."}), 404
    
    try:
        return send_from_directory(str(SITE_DIR), filename)
    except:
        return jsonify({"error": "File not found"}), 404


@app.route("/admin")
def admin_dashboard():
    """Serve the admin control panel"""
    site_status = "✅ Online" if (SITE_DIR.exists() and (SITE_DIR / "index.html").exists()) else "🚨 Offline"
    return render_template('admin_dashboard.html', site_status=site_status)


@app.route("/save-content", methods=["POST"])
@require_auth
def save_content():
    if not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON body"}), 400
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "Payload must be JSON object"}), 400

    try:
        CONTENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONTENT_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"status": "error", "message": f"Failed to write file: {exc}"}), 500

    try:
        files_uploaded = backup_site_to_b2(load_env_vars=False)
        update_metrics("backup", "success", files_uploaded)
        message = "Changes saved and backed up to cloud successfully!"
        if files_uploaded == 0:
            message += " (No files changed since last backup.)"
        return jsonify({"status": "success", "message": message})
    except Exception as exc:
        update_metrics("backup", "error", 0, str(exc))
        logging.exception("Automatic backup failed after saving content.json")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Content saved locally, but automatic backup failed: {exc}",
                }
            ),
            500,
        )


@app.route("/backup", methods=["POST"])
@require_auth
def trigger_backup():
    ok, message = run_script("backup_to_b2.py")
    status = "success" if ok else "error"
    if ok:
        update_metrics("backup", "success")
    else:
        update_metrics("backup", "error", 0, message)
    return jsonify({"status": status, "message": message}), (200 if ok else 500)


@app.route("/restore", methods=["POST"])
@require_auth
def trigger_restore():
    ok, message = run_script("restore_from_b2.py")
    status = "success" if ok else "error"
    if ok:
        update_metrics("restore", "success")
    else:
        update_metrics("restore", "error", 0, message)
    return jsonify({"status": status, "message": message}), (200 if ok else 500)


@app.route("/simulate-disaster", methods=["POST"])
@require_auth
def simulate_disaster():
    """Simulate a disaster by backing up and then removing the docs directory"""
    try:
        # First, ensure we have a recent backup
        logging.info("Creating safety backup before disaster simulation...")
        files_uploaded = backup_site_to_b2(load_env_vars=False)
        
        # Create local backup
        if BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)
        shutil.copytree(SITE_DIR, BACKUP_DIR)
        
        # Simulate disaster - remove docs directory
        logging.warning("SIMULATING DISASTER: Removing docs directory")
        shutil.rmtree(SITE_DIR)
        
        update_metrics("disaster", "simulated")
        return jsonify({
            "status": "success", 
            "message": f"🚨 Disaster simulated! Docs directory removed. {files_uploaded} files were backed up to cloud. Use Restore to recover."
        })
    except Exception as exc:
        logging.exception("Failed to simulate disaster")
        return jsonify({
            "status": "error", 
            "message": f"Failed to simulate disaster: {exc}"
        }), 500

@app.route("/metrics", methods=["GET"])
def get_metrics():
    """Get backup/restore metrics"""
    try:
        if METRICS_PATH.exists():
            with open(METRICS_PATH, "r") as f:
                metrics = json.load(f)
        else:
            metrics = {
                "last_backup": "—",
                "last_restore": "—",
                "backup_status": "Idle",
                "total_files": 0
            }
        return jsonify(metrics)
    except Exception as exc:
        logging.exception("Failed to get metrics")
        return jsonify({"error": str(exc)}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "site_dir_exists": SITE_DIR.exists(),
        "content_file_exists": CONTENT_PATH.exists()
    })

if __name__ == "__main__":
    # Ensure directories exist
    SITE_DIR.mkdir(exist_ok=True)
    CONTENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    logging.info("Starting Automated Cloud Backup server...")
    app.run(debug=True, use_reloader=False)



