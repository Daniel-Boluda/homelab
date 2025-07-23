import logging
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.util.percentage import int_states_in_range, percentage_to_ranged_value
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)
DOMAIN = "custom_fan"
SPEED_RANGE = (1, 6)  # 6 speeds

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Custom Fan platform with multiple entities."""
    _LOGGER.debug("Setting up custom fan platform.")
    fans = [
        CustomFan("ventilador_salon", "ventilador_salon", "interruptor_ventilador_salon"),
        CustomFan("ventilador_dormitorio", "ventilador_dormitorio", "interruptor_ventilador_dormitorio"),
        CustomFan("ventilador_invitados", "ventilador_invitados", "interruptor_ventilador_invitados"),
        CustomFan("ventilador_despacho", "ventilador_despacho", "interruptor_ventilador_despacho")
    ]
    async_add_entities(fans)
    _LOGGER.info("Custom fan platform setup complete with %d fans.", len(fans))

class CustomFan(FanEntity):
    def __init__(self, name, unique_id, interruptor_name):
        """Initialize the fan."""
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_is_on = False
        self._attr_percentage = 20  # Initialize with speed 1
        self._attr_supported_features = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.DIRECTION
            | FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
        )
        self._attr_direction = "forward"
        self._interruptor_name = interruptor_name
        _LOGGER.debug("Initialized fan: %s with unique ID: %s and interruptor: %s", name, unique_id, interruptor_name)

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        async_track_state_change_event(
            self.hass, [f"fan.{self._interruptor_name}"], self._async_interruptor_changed
        )

    @callback
    async def _async_interruptor_changed(self, event):
        """Handle interruptor state changes."""
        new_state = event.data.get('new_state')
        if new_state is None:
            return

        _LOGGER.debug("Interruptor state changed: %s", new_state.state)

        if new_state.state == 'on' and not self._attr_is_on:
            await self.async_turn_on()
        elif new_state.state == 'off' and self._attr_is_on:
            await self.async_turn_off()
        elif self._attr_is_on:
            new_percentage = new_state.attributes.get('percentage')
            if new_percentage is not None and new_percentage != self._attr_percentage:
                await self.async_set_percentage(new_percentage)
                self.async_write_ha_state()

    @property
    def icon(self):
        return "local:custom_fan"

    @property
    def state(self):
        """Return the state of the fan."""
        return "on" if self._attr_is_on else "off"

    @property
    def percentage(self):
        """Return the current speed percentage."""
        return self._attr_percentage

    @property
    def speed_count(self):
        """Return the number of speeds the fan supports."""
        return int_states_in_range(SPEED_RANGE)

    async def async_set_percentage(self, percentage):
        """Set the speed of the fan, as a percentage."""
        _LOGGER.debug("Setting percentage for %s to %d%%", self._attr_name, percentage)
        self._attr_percentage = percentage

        speed_command = f"velocidad_{self.calculate_speed(percentage)}"
        await self.send_command(speed_command)
        _LOGGER.info("Fan %s set to speed command: %s", self._attr_name, speed_command)
        
        # Update the interruptor speed
        await self._update_interruptor_speed(percentage)

    async def _update_interruptor_speed(self, percentage):
        """Update the interruptor speed."""
        service_data = {
            "entity_id": f"fan.{self._interruptor_name}",
            "percentage": percentage
        }
        try:
            await self.hass.services.async_call("fan", "set_percentage", service_data)
            _LOGGER.info("Updated interruptor %s speed to %d%%", self._interruptor_name, percentage)
        except Exception as e:
            _LOGGER.error("Failed to update interruptor %s speed: %s", self._interruptor_name, e)

    def calculate_speed(self, percentage):
        """Calculate speed based on percentage, clamped to SPEED_RANGE."""
        raw_speed = percentage_to_ranged_value(SPEED_RANGE, percentage)
        speed = round(raw_speed)
        speed = max(SPEED_RANGE[0], min(speed, SPEED_RANGE[1]))
        _LOGGER.debug("Calculated speed for percentage %d is %d", percentage, speed)
        return speed

    async def async_set_direction(self, direction: str):
        """Set the direction of the fan."""
        _LOGGER.debug("Setting direction for %s to %s", self._attr_name, direction)
        self._attr_direction = direction
        await self.send_command("reverse")
        _LOGGER.info("Fan %s direction set to %s", self._attr_name, direction)

    async def async_turn_on(self, percentage: int = None, preset_mode: str = None, **kwargs):
        """Turn on the fan."""
        _LOGGER.info("Turning on fan: %s", self._attr_name)
        self._attr_is_on = True
        
        if percentage is not None:
            _LOGGER.debug("Received percentage for %s: %d%%", self._attr_name, percentage)
            await self.async_set_percentage(percentage)
        else:
            await self.async_set_percentage(self._attr_percentage)
        
        self.async_write_ha_state()

        # Update the interruptor state
        await self._update_interruptor_state(True)

    async def async_turn_off(self, **kwargs):
        """Turn the fan off."""
        _LOGGER.info("Turning off fan: %s", self._attr_name)
        await self.send_command("off")
        
        self._attr_is_on = False
        self.async_write_ha_state()

        # Update the interruptor state
        await self._update_interruptor_state(False)

    async def _update_interruptor_state(self, is_on):
        """Update the interruptor state."""
        service_data = {
            "entity_id": f"fan.{self._interruptor_name}",
        }
        service = "turn_on" if is_on else "turn_off"
        try:
            await self.hass.services.async_call("fan", service, service_data)
            _LOGGER.info("Updated interruptor %s state to %s", self._interruptor_name, "on" if is_on else "off")
        except Exception as e:
            _LOGGER.error("Failed to update interruptor %s state: %s", self._interruptor_name, e)

    async def send_command(self, command):
        """Send command to Broadlink device."""
        service_data = {
            "entity_id": "remote.rfbridge_broadlink",
            "device": self._attr_unique_id,
            "command": command,
            "num_repeats": 1,
            "delay_secs": 0.4
        }
        
        _LOGGER.debug("Sending command '%s' to device '%s'", command, self._attr_unique_id)
        
        try:
            await self.hass.services.async_call("remote", "send_command", service_data)
            _LOGGER.info("Command '%s' sent successfully to device '%s'", command, self._attr_unique_id)
        except Exception as e:
            _LOGGER.error("Failed to send command '%s' to device '%s': %s", command, self._attr_unique_id, e)