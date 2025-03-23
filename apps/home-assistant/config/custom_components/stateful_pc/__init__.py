import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

_LOGGER = logging.getLogger(__name__)

DOMAIN = "stateful_pc"

async def async_setup(hass: HomeAssistant, config):
    """Set up the stateful_pc domain."""
    _LOGGER.debug("Setting up stateful_pc domain.")
    hass.async_create_task(
        discovery.async_load_platform(hass, "switch", DOMAIN, {}, config)
    )
    return True
