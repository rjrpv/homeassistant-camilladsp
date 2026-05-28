__version__ = "1.0.1"

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

from camilladsp import CamillaClient, CamillaError

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .model import CDSPData

LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Error to indicate something wrong with the API."""

class InvalidUrl(Exception):
    """Error to indicate the provided URL is invalid."""

class CDSPClient:
    """Set up CamillaDSP using the official pycamilladsp library."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize CamillaDSP module."""
        self.hass = hass
        self._url = url

        # Parse URL to extract host and port
        parsed = urlparse(url)

        # Validate URL has a valid scheme
        if parsed.scheme not in ("http", "https", "ws", "wss", ""):
            raise InvalidUrl(f"Invalid URL scheme '{parsed.scheme}' in '{url}'. Use http://, https://, ws://, wss://, or a bare host:port.")

        # Validate URL has a valid hostname
        if not parsed.hostname:
            raise InvalidUrl(f"No hostname found in URL '{url}'. Expected format: host:port or http://host:port")

        # Validate port is specified and valid
        if parsed.port is None:
            raise InvalidUrl(f"No port found in URL '{url}'. Expected format: host:port or http://host:port")

        self._host = parsed.hostname
        self._port = parsed.port

        log = f"CamillaDSP client initialized for {self._host}:{self._port}"
        LOGGER.info(log)

        self._client = CamillaClient(self._host, self._port)

        md5 = hashlib.md5()
        md5.update(url.encode('utf-8'))
        self.cdsp_id = md5.hexdigest()[0:16]
        self.name = DOMAIN

        self._volume: float = 0
        self._mute: bool = False
        self._source: str = ""
        self._connected: bool = False

    async def async_set_volume(self, volume: float):
        """Set volume in dB using pycamilladsp."""
        await self.hass.async_add_executor_job(self._client.volume.set_main_volume, volume)
        self._volume = volume

    async def async_set_muted(self, muted: bool):
        """Set mute using pycamilladsp."""
        await self.hass.async_add_executor_job(self._client.volume.set_main_mute, muted)
        self._mute = muted

    async def async_select_source(self, source: str):
        """Select source by setting the config file path, then reloading."""
        # Set the new config file path
        await self.hass.async_add_executor_job(self._client.config.set_file_path, source)

        # Reload to apply the new config
        await self.hass.async_add_executor_job(self._client.general.reload)

        # Verify the config was applied by checking the filepath
        filepath = await self.hass.async_add_executor_job(self._client.config.file_path)

        # Check if the source matches
        if filepath == source or filepath.endswith("/" + source):
            self._source = source
        else:
            LOGGER.warning("Error setting active config file, got: %s, expected: %s", filepath, source)

    async def connect(self) -> None:
        """Connect to CamillaDSP and validate connectivity."""
        log = f"CamillaDSP connecting to {self._host}:{self._port}"
        LOGGER.info(log)

        try:
            # First establish the WebSocket connection
            await self.hass.async_add_executor_job(self._client.connect)
            # Then validate by fetching data
            await self.update()
            self._connected = True
            log = f"CamillaDSP connected successfully to {self._host}:{self._port}"
            LOGGER.info(log)
        except ApiError as e:
            self._connected = False
            log = f"CamillaDSP API error during connection: {e}"
            LOGGER.error(log)
            raise
        except Exception as e:
            self._connected = False
            log = f"CamillaDSP unable to connect to {self._host}:{self._port}: {e}"
            LOGGER.error(log)
            raise ApiError(f"Failed to connect to CamillaDSP at {self._host}:{self._port}: {e}") from e

    @property
    def connected(self) -> bool:
        """Return whether the client is connected."""
        return self._connected

    async def update(self) -> CDSPData:
        """Update CamillaDSP data through pycamilladsp."""
        state: MediaPlayerState = MediaPlayerState.OFF
        volume: float = 0
        mute: bool = False
        source: str = ""
        source_list: list[str] = []
        capturerate: int = 0

        try:
            # Get state via general.state()
            cdsp_state = await self.hass.async_add_executor_job(self._client.general.state)
            state = self._map_state(cdsp_state)

            if state != MediaPlayerState.OFF:
                # Get capture rate
                capturerate = await self.hass.async_add_executor_job(self._client.general.capture_rate)

                # Get volume via volume.main_volume()
                volume = float(await self.hass.async_add_executor_job(self._client.volume.main_volume))

                # Get mute via volume.main_mute()
                mute = bool(await self.hass.async_add_executor_job(self._client.volume.main_mute))

                # Get current config filepath via config.file_path()
                source = await self.hass.async_add_executor_job(self._client.config.file_path)

                # Note: There is no endpoint to enumerate stored configs.
                source_list = []

        except CamillaError as e:
            log = f"CamillaDSP API error from {self._host}:{self._port}: {e}"
            LOGGER.error(log)
            raise ApiError(f"CamillaDSP API error: {e}") from e
        except ConnectionRefusedError as e:
            log = f"CamillaDSP connection refused to {self._host}:{self._port}: {e}"
            LOGGER.error(log)
            raise ApiError(f"Connection refused to {self._host}:{self._port}: {e}") from e
        except IOError as e:
            log = f"CamillaDSP WebSocket error to {self._host}:{self._port}: {e}"
            LOGGER.error(log)
            raise ApiError(f"WebSocket error: {e}") from e
        except Exception as e:
            log = f"CamillaDSP unexpected error from {self._host}:{self._port}: {type(e).__name__}: {e}"
            LOGGER.error(log, exc_info=True)
            raise ApiError(f"Unexpected error from CamillaDSP: {type(e).__name__}: {e}") from e

        log = f"CamillaDSP update complete: state={state}, volume={volume}, source={source}"
        LOGGER.debug(log)

        return CDSPData(state=state,
                        volume=volume,
                        mute=mute,
                        source=source,
                        source_list=source_list,
                        capturerate=capturerate)

    def _map_state(self, cdsp_state: str) -> MediaPlayerState:
        """Map CamillaDSP state to MediaPlayerState."""
        state_map = {
            "inactive": MediaPlayerState.OFF,
            "paused": MediaPlayerState.PAUSED,
            "running": MediaPlayerState.PLAYING,
            "stalled": MediaPlayerState.IDLE,
            "starting": MediaPlayerState.ON,
        }
        LOGGER.info(f'Processing state: {cdsp_state}')
        return state_map.get(str(cdsp_state).lower(), MediaPlayerState.OFF)