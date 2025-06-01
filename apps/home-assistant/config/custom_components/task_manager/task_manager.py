import json
import os
from datetime import datetime, timedelta
from homeassistant.helpers.event import async_call_later
from .const import DOMAIN, TASKS_FILE

class TaskManager:
    def __init__(self, hass):
        self.hass = hass
        self.tasks = {}

    async def load_tasks(self):
        path = self.hass.config.path(TASKS_FILE)
        if os.path.exists(path):
            with open(path, "r") as f:
                self.tasks = json.load(f)
        else:
            self.tasks = {}

    async def save_tasks(self):
        path = self.hass.config.path(TASKS_FILE)
        with open(path, "w") as f:
            json.dump(self.tasks, f, indent=2)

    async def schedule_tasks(self):
        today = datetime.now().strftime("%A").lower()
        for task_id, task in self.tasks.items():
            if today in task["days"]:
                await self.send_notification(task_id, task)

    async def send_notification(self, task_id, task):
        message = f"Tarea para {task['user']}: {task['name']}"
        actions = [
            {"action": "done", "title": "✅ Hecha"},
            {"action": "skip", "title": "❌ Omitir"},
            {"action": "remind", "title": "⏰ Recordar más tarde"}
        ]
        await self.hass.services.async_call("notify", task["user_notify"], {
            "message": message,
            "title": "Tarea Programada",
            "data": {
                "tag": f"task_{task_id}",
                "actions": actions
            }
        })

    async def mark_task(self, task_id, status):
        task = self.tasks.get(task_id)
        if not task:
            return
        task.setdefault("history", []).append({
            "status": status,
            "timestamp": datetime.now().isoformat()
        })
        await self.save_tasks()

    async def remind_later(self, task_id):
        async_call_later(self.hass, 3600, lambda *_: self.hass.async_create_task(
            self.send_notification(task_id, self.tasks[task_id])
        ))

    async def add_or_update_task(self, task_id, task_data):
        self.tasks[task_id] = task_data
        await self.save_tasks()

    async def remove_task(self, task_id):
        if task_id in self.tasks:
            self.tasks.pop(task_id)
            await self.save_tasks()
