import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import CONF_HOST, CONF_MAC
from wakeonlan import send_magic_packet

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.debug("Setting up stateful PC platform.")

    host = config.get(CONF_HOST)
    mac = config.get(CONF_MAC)
    shutdown_ssh = config.get("shutdown_ssh", False)
    shutdown_user = config.get("shutdown_user")
    shutdown_command = config.get("shutdown_command")
    ssh_key = config.get("ssh_key")
    name = config.get("name", "PC")

    if not host or not mac:
        _LOGGER.error("Host and MAC must be provided in the configuration")
        return

    async_add_entities([PCSwitch(hass, name, host, mac, shutdown_ssh, shutdown_user, shutdown_command, ssh_key)])
    _LOGGER.info("stateful PC platform setup complete.")

class PCSwitch(SwitchEntity):
    """Representation of a PC switch with WoL and state tracking using SSH for shutdown."""

    def __init__(self, hass, name, host, mac, shutdown_ssh, shutdown_user, shutdown_command, ssh_key):
        self.hass = hass
        self._name = name
        self._host = host
        self._mac = mac
        self._shutdown_ssh = shutdown_ssh
        self._shutdown_user = shutdown_user
        self._shutdown_command = shutdown_command
        self._ssh_key = ssh_key
        self._state = False
        self._available = True

    @property
    def name(self):
        """Return the name of the PC."""
        return self._name

    @property
    def is_on(self):
        """Return True if the PC is on."""
        return self._state

    @property
    def available(self):
        """Return True if the PC is available (i.e. has been pinged successfully)."""
        return self._available

    async def async_turn_on(self, **kwargs):
        """Turn on the PC using Wake-on-LAN."""
        _LOGGER.info("Sending Wake-on-LAN magic packet to %s", self._name)
        try:
            send_magic_packet(self._mac)
            self._state = True  # Optimistically mark as on
        except Exception as e:
            _LOGGER.error("Error sending magic packet: %s", e)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the PC using an SSH shutdown command."""
        if self._shutdown_ssh and self._shutdown_user and self._shutdown_command and self._ssh_key:
            _LOGGER.info("Sending SSH shutdown command to %s", self._name)
            try:
                process = await asyncio.create_subprocess_exec(
                    "ssh", "-i", self._ssh_key,
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/config/.ssh/known_hosts",
                    f"{self._shutdown_user}@{self._host}",
                    self._shutdown_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    _LOGGER.error("SSH shutdown command failed: %s", stderr.decode().strip())
                else:
                    _LOGGER.info("SSH shutdown command succeeded: %s", stdout.decode().strip())
            except Exception as e:
                _LOGGER.error("Error executing SSH shutdown command: %s", e)
        else:
            _LOGGER.warning("SSH shutdown not properly configured for %s", self._name)
        self._state = False
        self.async_write_ha_state()

    async def async_update(self):
        """Ping the PC to update its state."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "1", self._host,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                self._state = True
                self._available = True
            else:
                self._state = False
                self._available = True
        except Exception as e:
            _LOGGER.error("Error pinging host %s: %s", self._host, e)
            self._available = False
