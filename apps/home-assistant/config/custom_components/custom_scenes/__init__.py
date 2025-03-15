import logging
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "custom_scenes"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("custom_scenes"): {cv.string: {cv.entity_id: cv.string}},
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    #Set up the Custom Scenes component
    scenes_config = config.get(DOMAIN, {}).get("custom_scenes", {})
    hass.data[DOMAIN] = scenes_config

    # Initialize the SceneManager
    from .custom_scenes import SceneManager

    manager = SceneManager(hass, scenes_config)
    await manager.initialize()
    hass.data[f"{DOMAIN}_manager"] = manager

    return True
