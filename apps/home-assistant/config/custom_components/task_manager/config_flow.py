import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN

TASK_SCHEMA = vol.Schema({
    vol.Required("task_id"): cv.slug,
    vol.Required("name"): str,
    vol.Required("user"): str,
    vol.Required("user_notify"): str,
    vol.Required("days"): vol.All(cv.ensure_list, [vol.In([
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
    ])])
})

class TaskManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self):
        self.tasks = {}

    async def async_step_init(self, user_input=None):
        # Cargar tareas actuales
        if not self.tasks:
            hass = self.hass
            tm = hass.data[DOMAIN]
            await tm.load_tasks()
            self.tasks = tm.tasks.copy()

        # Mostrar lista para elegir editar o crear nueva
        options = list(self.tasks.keys())
        options.append("create_new")

        if user_input is None:
            return self.async_show_menu(
                step_id="init",
                menu_options=options,
                menu_titles={**{k: f"Editar: {self.tasks[k]['name']}" for k in self.tasks}, "create_new": "Crear tarea nueva"}
            )

        if user_input == "create_new":
            return await self.async_step_user()

        # Si eligió editar
        self.edit_task_id = user_input
        task = self.tasks[self.edit_task_id]
        return await self.async_step_user(preload=task, task_id=self.edit_task_id)

    async def async_step_user(self, user_input=None, preload=None, task_id=None):
        errors = {}

        schema = TASK_SCHEMA
        if preload:
            # Preload values
            schema = vol.Schema({
                vol.Required("task_id", default=task_id): cv.slug,
                vol.Required("name", default=preload.get("name", "")): str,
                vol.Required("user", default=preload.get("user", "")): str,
                vol.Required("user_notify", default=preload.get("user_notify", "")): str,
                vol.Required("days", default=preload.get("days", [])): vol.All(cv.ensure_list, [vol.In([
                    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
                ])])
            })

        if user_input is not None:
            task_id_final = user_input["task_id"]

            # Validar que el slug no choque si es nuevo
            if task_id_final != task_id and task_id_final in self.tasks:
                errors["task_id"] = "already_configured"
            else:
                # Guardar tarea
                new_task = {
                    "name": user_input["name"],
                    "user": user_input["user"],
                    "user_notify": user_input["user_notify"],
                    "days": user_input["days"],
                    "history": self.tasks.get(task_id_final, {}).get("history", [])
                }

                tm = self.hass.data[DOMAIN]
                await tm.add_or_update_task(task_id_final, new_task)
                self.tasks = tm.tasks.copy()

                return self.async_create_entry(title=user_input["name"], data={"task_id": task_id_final})

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors
        )

    async def async_step_delete(self, user_input=None):
        # Paso para eliminar tarea
        if user_input is not None and user_input.get("confirm"):
            tm = self.hass.data[DOMAIN]
            await tm.remove_task(self.edit_task_id)
            self.tasks.pop(self.edit_task_id, None)
            return self.async_create_entry(title="Tarea eliminada", data={})

        return self.async_show_form(
            step_id="delete",
            data_schema=vol.Schema({
                vol.Required("confirm", default=False): bool
            }),
            description_placeholders={"task": self.edit_task_id}
        )
