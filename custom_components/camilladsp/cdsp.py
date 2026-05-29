__version__ = "1.0.1"

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

try:
    from camilladsp import CamillaClient, CamillaError, ProcessingState
except ImportError as e:
    logging.getLogger("custom_components.camilladsp").critical(
        "CamillaDSP: failed to import pycamilladsp library: %s: %s", type(e).__name__, e
    )
    raise

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
        LOGGER.info("CamillaDSP CDSPClient init: parsed url='%s', scheme='%s', hostname=%s, port=%s", url, parsed.scheme, parsed.hostname, parsed.port)

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

        LOGGER.info("CamillaDSP CDSPClient initialized: host=%s, port=%s", self._host, self._port)

        LOGGER.info("CamillaDSP creating CamillaClient(%s, %s)...", self._host, self._port)
        self._client = CamillaClient(self._host, self._port)
        LOGGER.info("CamillaDSP CamillaClient created successfully")

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
        LOGGER.info("CamillaDSP connect() called for %s:%s", self._host, self._port)

        try:
            # First establish the WebSocket connection
            LOGGER.info("CamillaDSP calling _client.connect...")
            await self.hass.async_add_executor_job(self._client.connect)
            LOGGER.info("CamillaDSP _client.connect returned successfully")
            # Then validate by fetching data
            LOGGER.info("CamillaDSP calling update() to validate connection...")
            await self.update()
            self._connected = True
            LOGGER.info("CamillaDSP connected successfully to %s:%s", self._host, self._port)
        except ApiError as e:
            self._connected = False
            LOGGER.error("CamillaDSP API error during connection: %s", e)
            raise
        except Exception as e:
            self._connected = False
            LOGGER.error("CamillaDSP unable to connect to %s:%s: %s: %s", self._host, self._port, type(e).__name__, e, exc_info=True)
            raise ApiError(f"Failed to connect to CamillaDSP at {self._host}:{self._port}: {type(e).__name__}: {e}") from e

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
            LOGGER.info("CamillaDSP update: calling _client.general.state...")
            cdsp_state = await self.hass.async_add_executor_job(self._client.general.state)
            LOGGER.info("CamillaDSP update: general.state returned '%s'", cdsp_state)
            state = self._map_state(cdsp_state)
            LOGGER.info("CamillaDSP update: mapped state to %s", state)

            if state != MediaPlayerState.OFF:
                # Get capture rate
                LOGGER.info("CamillaDSP update: calling _client.rate.capture...")
                capturerate = await self.hass.async_add_executor_job(self._client.rate.capture)
                LOGGER.info("CamillaDSP update: capture_rate=%s", capturerate)

                # Get volume via volume.main_volume()
                LOGGER.info("CamillaDSP update: calling _client.volume.main_volume...")
                volume = float(await self.hass.async_add_executor_job(self._client.volume.main_volume))
                LOGGER.info("CamillaDSP update: volume=%s", volume)

                # Get mute via volume.main_mute()
                LOGGER.info("CamillaDSP update: calling _client.volume.main_mute...")
                mute = bool(await self.hass.async_add_executor_job(self._client.volume.main_mute))
                LOGGER.info("CamillaDSP update: mute=%s", mute)

                # Get current config filepath via config.file_path()
                LOGGER.info("CamillaDSP update: calling _client.config.file_path...")
                source = await self.hass.async_add_executor_job(self._client.config.file_path)
                LOGGER.info("CamillaDSP update: source=%s", source)

                # Note: There is no endpoint to enumerate stored configs.
                source_list = []

        except CamillaError as e:
            LOGGER.error("CamillaDSP API error from %s:%s: %s", self._host, self._port, e)
            raise ApiError(f"CamillaDSP API error: {e}") from e
        except ConnectionRefusedError as e:
            LOGGER.error("CamillaDSP connection refused to %s:%s: %s", self._host, self._port, e)
            raise ApiError(f"Connection refused to {self._host}:{self._port}: {e}") from e
        except IOError as e:
            LOGGER.error("CamillaDSP WebSocket error to %s:%s: %s", self._host, self._port, e)
            raise ApiError(f"WebSocket error: {e}") from e
        except Exception as e:
            LOGGER.error("CamillaDSP unexpected error from %s:%s: %s: %s", self._host, self._port, type(e).__name__, e, exc_info=True)
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
            # "inactive"
            ProcessingState.INACTIVE: MediaPlayerState.OFF,
            # "paused"
            ProcessingState.PAUSED: MediaPlayerState.PAUSED,
            # "running"
            ProcessingState.RUNNING: MediaPlayerState.PLAYING,
            # "stalled"
            ProcessingState.STALLED: MediaPlayerState.IDLE,
            # "starting"
            ProcessingState.STARTING: MediaPlayerState.ON,
        }
        LOGGER.info(f'Processing state: {cdsp_state}')
        return state_map.get(cdsp_state, MediaPlayerState.OFF)