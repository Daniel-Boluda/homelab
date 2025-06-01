from homeassistant.helpers.entity import Entity

class TaskSensor(Entity):
    def __init__(self, hass, task_id, task):
        self.hass = hass
        self._task_id = task_id
        self._task = task
        self._attr_name = f"Tarea: {task['name']}"
        self._state = "Pendiente"

    @property
    def unique_id(self):
        return f"task_manager_{self._task_id}"

    @property
    def name(self):
        return self._attr_name

    @property
    def state(self):
        if self._task.get("history"):
            last_status = self._task["history"][-1]["status"]
            return last_status.capitalize()
        return "Pendiente"

    @property
    def extra_state_attributes(self):
        return {
            "user": self._task["user"],
            "days": self._task["days"],
            "last_update": self._task["history"][-1]["timestamp"] if self._task.get("history") else None,
        }

    async def async_update(self):
        # Refrescar estado leyendo el archivo de tareas para sincronizar
        await self.hass.data["task_manager"].load_tasks()
        task = self.hass.data["task_manager"].tasks.get(self._task_id)
        if task:
            self._task = task
