from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from .const import DOMAIN
from .task_manager import TaskManager
from .sensor import TaskSensor

async def async_setup(hass: HomeAssistant, config: dict):
    tm = TaskManager(hass)
    await tm.load_tasks()
    hass.data[DOMAIN] = tm

    # Registrar servicios
    async def mark_done(call):
        await tm.mark_task(call.data["task_id"], "done")

    async def mark_skipped(call):
        await tm.mark_task(call.data["task_id"], "skipped")

    async def remind_later(call):
        await tm.remind_later(call.data["task_id"])

    hass.services.async_register(DOMAIN, "mark_done", mark_done)
    hass.services.async_register(DOMAIN, "mark_skipped", mark_skipped)
    hass.services.async_register(DOMAIN, "remind_later", remind_later)

    # Crear sensores para cada tarea
    async def async_add_sensors():
        sensors = []
        for task_id, task in tm.tasks.items():
            sensors.append(TaskSensor(hass, task_id, task))

        platform = entity_platform.current_platform.get()
        platform.async_register_entities(sensors)

    hass.async_create_task(async_add_sensors())

    return True
