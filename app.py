import os
import json
import asyncio
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import requests as req

from scraper import LMSScraper
from notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "lms-polinema-secret-2024")

# In-memory storage (Railway persistent via env var backup)
state = {
    "credentials": {
        "username": os.environ.get("SIAKAD_USERNAME", ""),
        "password": os.environ.get("SIAKAD_PASSWORD", ""),
        "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    },
    "tasks": [],
    "last_check": None,
    "is_running": False,
    "status": "idle",
    "log": []
}

scheduler = BackgroundScheduler()

def add_log(message, level="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    state["log"].insert(0, {"time": timestamp, "msg": message, "level": level})
    state["log"] = state["log"][:50]  # keep last 50
    logger.info(message)

def check_tasks_job():
    if state["is_running"]:
        add_log("⏭️ Scraping sudah berjalan, skip...", "warn")
        return
    
    creds = state["credentials"]
    if not creds["username"] or not creds["password"]:
        add_log("❌ Credentials belum diisi!", "error")
        return

    state["is_running"] = True
    state["status"] = "scraping"
    add_log("🔍 Mulai cek tugas baru...")

    try:
        scraper = LMSScraper()
        new_tasks = scraper.get_tasks(creds["username"], creds["password"])
        
        # Find truly new tasks
        existing_ids = {t.get("id") for t in state["tasks"]}
        added = [t for t in new_tasks if t.get("id") not in existing_ids]
        
        state["tasks"] = new_tasks
        state["last_check"] = datetime.now().strftime("%d %b %Y %H:%M")
        
        if added:
            add_log(f"🆕 {len(added)} tugas baru ditemukan!", "success")
            if creds["telegram_token"] and creds["telegram_chat_id"]:
                notifier = TelegramNotifier(creds["telegram_token"], creds["telegram_chat_id"])
                notifier.send_new_tasks(added)
                add_log(f"📨 Notifikasi Telegram terkirim!", "success")
        else:
            add_log(f"✅ Tidak ada tugas baru. Total: {len(new_tasks)} tugas", "info")
            
        state["status"] = "idle"
    except Exception as e:
        add_log(f"❌ Error: {str(e)}", "error")
        state["status"] = "error"
    finally:
        state["is_running"] = False


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    return jsonify({
        "status": state["status"],
        "last_check": state["last_check"],
        "task_count": len(state["tasks"]),
        "tasks": state["tasks"],
        "log": state["log"],
        "scheduler_running": scheduler.running,
        "has_credentials": bool(state["credentials"]["username"]),
        "has_telegram": bool(state["credentials"]["telegram_token"]),
    })

@app.route("/api/credentials", methods=["POST"])
def save_credentials():
    data = request.json
    state["credentials"].update({
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "telegram_token": data.get("telegram_token", ""),
        "telegram_chat_id": data.get("telegram_chat_id", ""),
    })
    add_log("💾 Credentials disimpan", "success")
    return jsonify({"ok": True})

@app.route("/api/check", methods=["POST"])
def manual_check():
    thread = threading.Thread(target=check_tasks_job)
    thread.daemon = True
    thread.start()
    return jsonify({"ok": True, "message": "Scraping dimulai..."})

@app.route("/api/test-telegram", methods=["POST"])
def test_telegram():
    creds = state["credentials"]
    if not creds["telegram_token"] or not creds["telegram_chat_id"]:
        return jsonify({"ok": False, "message": "Token atau Chat ID belum diisi"})
    try:
        notifier = TelegramNotifier(creds["telegram_token"], creds["telegram_chat_id"])
        notifier.send_test()
        add_log("📨 Test Telegram terkirim!", "success")
        return jsonify({"ok": True, "message": "Pesan test berhasil dikirim!"})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)})

@app.route("/api/scheduler", methods=["POST"])
def toggle_scheduler():
    data = request.json
    interval = data.get("interval", 30)  # minutes
    
    # Remove existing job if any
    try:
        scheduler.remove_job("check_tasks")
    except:
        pass
    
    if data.get("enable"):
        scheduler.add_job(check_tasks_job, "interval", minutes=interval, id="check_tasks")
        if not scheduler.running:
            scheduler.start()
        add_log(f"⏰ Auto-check aktif setiap {interval} menit", "success")
        return jsonify({"ok": True, "message": f"Auto-check aktif setiap {interval} menit"})
    else:
        add_log("⏹️ Auto-check dinonaktifkan", "warn")
        return jsonify({"ok": True, "message": "Auto-check dinonaktifkan"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
