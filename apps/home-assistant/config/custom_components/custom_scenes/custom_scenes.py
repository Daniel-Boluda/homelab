import json
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change

class SceneManager:
    def __init__(self, hass: HomeAssistant, scenes: dict):
        """Initialize the SceneManager."""
        self.hass = hass
        self.scenes = scenes
        self.listeners = []

    async def initialize(self):
        """Initialize the component."""
        await self.create_scene_booleans()
        self.setup_listeners()

    async def create_scene_booleans(self):
        """Create input booleans for each scene."""
        for scene_name in self.scenes:
            entity_id = f"input_boolean.{scene_name}_status"
            if not self.hass.states.get(entity_id):
                await self.hass.services.async_call(
                    "input_boolean",
                    "create",
                    {"name": f"{scene_name} Status", "object_id": f"{scene_name}_status"},
                )

    def setup_listeners(self):
        """Set up listeners for state changes."""
        # Listen for entity state changes
        all_entities = {entity for scene in self.scenes.values() for entity in scene}
        async_track_state_change(
            self.hass, list(all_entities), self.handle_entity_state_change
        )

        # Listen for scene status changes (input_boolean)
        scene_booleans = [f"input_boolean.{scene}_status" for scene in self.scenes]
        async_track_state_change(
            self.hass, scene_booleans, self.handle_scene_status_change
        )

    async def handle_entity_state_change(self, entity_id, old_state, new_state):
        """Handle changes in entity states."""
        for scene_name, entities in self.scenes.items():
            target_states_match = all(
                self.hass.states.get(entity).state == target_state
                for entity, target_state in entities.items()
            )
            scene_status_entity = f"input_boolean.{scene_name}_status"
            current_scene_status = (
                self.hass.states.get(scene_status_entity).state == "on"
            )

            if target_states_match and not current_scene_status:
                await self.toggle_scene(scene_name, True)
            elif not target_states_match and current_scene_status:
                await self.toggle_scene(scene_name, False)

    async def handle_scene_status_change(self, entity_id, old_state, new_state):
        """Handle manual changes to scene status."""
        scene_name = entity_id.split(".")[1].replace("_status", "")
        if new_state.state == "on":
            await self.activate_scene(scene_name)
        else:
            await self.deactivate_scene(scene_name)

    async def toggle_scene(self, scene_name: str, activate: bool):
        """Toggle the input_boolean representing the scene status."""
        await self.hass.services.async_call(
            "input_boolean",
            "turn_on" if activate else "turn_off",
            {"entity_id": f"input_boolean.{scene_name}_status"},
        )

    async def activate_scene(self, scene_name: str):
        """Activate a scene by setting all entities to their target states."""
        entities = self.scenes[scene_name]
        for entity_id, target_state in entities.items():
            await self.hass.services.async_call(
                "homeassistant",
                f"turn_{target_state}",
                {"entity_id": entity_id},
            )

    async def deactivate_scene(self, scene_name: str):
        """Deactivate a scene (optional behavior can be added here)."""
        _LOGGER.info(f"Scene {scene_name} deactivated.")
