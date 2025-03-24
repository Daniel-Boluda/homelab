import asyncio
import logging
import aiohttp

from homeassistant.components.switch import SwitchEntity
from wakeonlan import send_magic_packet

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.debug("Setting up stateful PC platform.")

    host = config.get("host")
    wol_mode = config.get("wol_mode","host")
    wol_apiurl = config.get("wol_apiurl")
    wol_apikey = config.get("wol_apikey")
    wol_mac = config.get("wol_mac")
    wol_broadcast_address = config.get("wol_broadcast_address")
    wol_port = config.get("wol_port")
    shutdown_ssh = config.get("shutdown_ssh", False)
    shutdown_user = config.get("shutdown_user")
    shutdown_command = config.get("shutdown_command")
    ssh_key = config.get("ssh_key")
    name = config.get("name", "PC")

    if not host or not wol_mac:
        _LOGGER.error("Host and MAC must be provided in the configuration")
        return

    async_add_entities([PCSwitch(hass, name, host,wol_mode ,wol_apiurl ,wol_apikey , wol_mac,wol_broadcast_address,wol_port, shutdown_ssh, shutdown_user, shutdown_command, ssh_key)])
    _LOGGER.info("stateful PC platform setup complete.")

class PCSwitch(SwitchEntity):
    """Representation of a PC switch with WoL and state tracking using SSH for shutdown."""

    def __init__(self, hass, name, host,wol_mode ,wol_apiurl ,wol_apikey , wol_mac,wol_broadcast_address,wol_port, shutdown_ssh, shutdown_user, shutdown_command, ssh_key):
        self.hass = hass
        self._name = name
        self._host = host
        
        #wol_mode can be "host" or "wol-api"
        self._wol_mode = wol_mode
        
        self._wol_mac = wol_mac
        
        #"wol-api" params
        self._wol_apiurl = wol_apiurl
        self._wol_apikey = wol_apikey
        
        #"host" params
        self._wol_broadcast_address = wol_broadcast_address
        self._wol_port = wol_port
        
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
        _LOGGER.info("Attempting to send Wake-on-LAN signal to %s", self._name)
        try:
            if self._wol_mode == "wol-api":
                _LOGGER.info("Using WOL-API mode to turn on the PC.")
                # Ensure the URL starts with http:// or https://
                if not self._wol_apiurl.startswith(("http://", "https://")):
                    api_url = "http://" + self._wol_apiurl
                    _LOGGER.debug("API URL was missing scheme, defaulting to http://")
                else:
                    api_url = self._wol_apiurl
                    _LOGGER.debug("Using provided API URL: %s", api_url)
                
                # Use the /wake endpoint and send the MAC address in JSON payload
                full_url = f"{api_url}/wake"
                _LOGGER.debug("Constructed WOL-API URL: %s", full_url)
                
                # Set headers with X-API-Key as expected by the Flask app
                headers = {"X-API-Key": self._wol_apikey} if self._wol_apikey else {}
                _LOGGER.debug("Sending request with headers: %s", headers)
                
                # Use the key "mac_address" to match the Flask API
                payload = {"mac_address": self._wol_mac}
                _LOGGER.debug("Payload for WOL-API request: %s", payload)

                async with aiohttp.ClientSession() as session:
                    async with session.post(full_url, headers=headers, json=payload) as response:
                        if response.status != 200:
                            _LOGGER.error("WOL-API request failed with status %s", response.status)
                            _LOGGER.error("Response body: %s", await response.text())
                        else:
                            _LOGGER.info("WOL-API request succeeded with status %s", response.status)
            else:
                _LOGGER.info("Using local Wake-on-LAN packet method.")
                # Fallback to using local Wake-on-LAN packet
                send_magic_packet(self._wol_mac, ip_address=self._wol_broadcast_address, port=self._wol_port)
                _LOGGER.debug("Sent magic packet to %s at %s:%s", self._wol_mac, self._wol_broadcast_address, self._wol_port)

            self._state = True  # Optimistically mark as on
            _LOGGER.info("PC is now on (optimistic state).")
        except Exception as e:
            _LOGGER.error("Error sending wake signal: %s", e)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the PC using an SSH shutdown command."""
        if self._shutdown_ssh and self._shutdown_user and self._shutdown_command and self._ssh_key:
            _LOGGER.info("Sending SSH shutdown command to %s", self._name)
            try:
                process = await asyncio.create_subprocess_exec(
                    "ssh", "-i", self._ssh_key,
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
