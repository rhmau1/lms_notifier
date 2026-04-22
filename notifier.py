import requests
import time
from datetime import datetime


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send(self, message: str):
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()

    def send_test(self):
        msg = (
            "🎓 <b>LMS Notifier Polinema</b>\n\n"
            "✅ Koneksi Telegram berhasil!\n"
            "Bot kamu sudah aktif dan siap mengirim notifikasi tugas.\n\n"
            f"🕐 <i>{datetime.now().strftime('%d %b %Y %H:%M')}</i>"
        )
        self.send(msg)

    def send_new_tasks(self, tasks: list[dict]):
        if not tasks:
            return

        if len(tasks) == 1:
            msg = self._format_single_task(tasks[0])
        else:
            msg = self._format_multiple_tasks(tasks)

        self.send(msg)

    def _urgency_icon(self, task: dict) -> str:
        """Return emoji based on days until deadline"""
        ts = task.get("deadline_ts", 0)
        if not ts:
            return "📝"
        now = time.time()
        days_left = (ts - now) / 86400
        if days_left < 0:
            return "🔴"  # overdue
        elif days_left <= 3:
            return "🔴"  # urgent
        elif days_left <= 7:
            return "🟡"  # soon
        else:
            return "🟢"  # ok

    def _format_single_task(self, task: dict) -> str:
        icon = self._urgency_icon(task)
        title = task.get("title", "Tugas tidak diketahui")
        course = task.get("course", "-")
        deadline = task.get("deadline", "-")
        link = task.get("link", "")

        msg = (
            f"📚 <b>Tugas Baru Ditemukan!</b>\n\n"
            f"{icon} <b>{title}</b>\n"
            f"🏫 {course}\n"
            f"⏰ Deadline: <b>{deadline}</b>\n"
        )
        if link:
            msg += f'🔗 <a href="{link}">Buka Tugas</a>\n'
        return msg

    def _format_multiple_tasks(self, tasks: list[dict]) -> str:
        msg = f"📚 <b>{len(tasks)} Tugas Baru Ditemukan!</b>\n\n"
        for i, task in enumerate(tasks, 1):
            icon = self._urgency_icon(task)
            title = task.get("title", "?")
            course = task.get("course", "-")
            deadline = task.get("deadline", "-")
            link = task.get("link", "")

            if link:
                msg += f'{i}. {icon} <a href="{link}"><b>{title}</b></a>\n'
            else:
                msg += f"{i}. {icon} <b>{title}</b>\n"
            msg += f"   🏫 {course}\n"
            msg += f"   ⏰ {deadline}\n\n"

        return msg

    def send_deadline_reminder(self, tasks: list[dict]):
        if not tasks:
            return
        msg = "⚠️ <b>Pengingat Deadline!</b>\n\n"
        for task in tasks:
            icon = self._urgency_icon(task)
            msg += f"{icon} <b>{task['title']}</b>\n"
            msg += f"  ⏰ {task['deadline']}\n\n"
        self.send(msg)