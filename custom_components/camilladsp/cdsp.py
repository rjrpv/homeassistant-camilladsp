__version__ = "1.0.1"

import hashlib
import json
import logging
from typing import Any

import aiohttp
from aiohttp import ClientResponseError

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .model import CDSPData

LOGGER = logging.getLogger(__name__)

# Default timeout for API calls in seconds
API_TIMEOUT = 10


class ApiError(Exception):
    """Error to indicate something wrong with the API."""

class CDSPClient:
    """Set up CamillaDSP."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize CamillaDSP module."""
        self.hass = hass
        self.url = url
        self.status: dict = {}

        md5 = hashlib.md5()
        md5.update(url.encode('utf-8'))
        self.cdsp_id = md5.hexdigest()[0:16]
        self.name = DOMAIN

        self._volume: float = 0
        self._mute: bool = False
        self._source: str = ""
        self._connected: bool = False

    async def async_set_volume(self, volume: float):
        """Set volume in dB using POST /api/volume/set."""
        await self.async_post_api(endpoint="volume/set", data={"volume": volume})
        self._volume = volume

    async def async_set_muted(self, muted: bool):
        """Set mute using POST /api/mute/set."""
        await self.async_post_api(endpoint="mute/set", data={"muted": muted})
        self._mute = muted

    async def async_select_source(self, source: str):
        """Select source by setting the config file path via POST /api/config/set/filepath, then POST /api/config/reload."""
        # Set the new config file path
        await self.async_post_api(endpoint="config/set/filepath", data={"filePath": source})

        # Reload to apply the new config
        await self.async_post_api(endpoint="config/reload", data={})

        # Verify the config was applied by checking the filepath
        configData = await self.async_get_api(endpoint="config/filepath")
        filepath = json.loads(configData).get("filePath", "")

        # Check if the source matches (compare basename or full path)
        if filepath == source or filepath.endswith("/" + source):
            self._source = source
        else:
            LOGGER.warning("Error setting active config file, got: %s, expected: %s", filepath, source)

    async def connect(self) -> None:
        """Connect to CamillaDSP API and validate connectivity."""
        log = f"CamillaDSP connecting to {self.url}"
        LOGGER.info(log)

        try:
            await self.update()
            self._connected = True
            log = f"CamillaDSP connected successfully to {self.url}"
            LOGGER.info(log)
        except ApiError as e:
            self._connected = False
            log = f"CamillaDSP API error during connection: {e}"
            LOGGER.error(log)
            raise
        except Exception as e:
            self._connected = False
            log = f"CamillaDSP unable to connect to {self.url}: {e}"
            LOGGER.error(log)
            raise ApiError(f"Failed to connect to CamillaDSP at {self.url}: {e}") from e

    @property
    def connected(self) -> bool:
        """Return whether the client is connected."""
        return self._connected


    async def update(self) -> CDSPData:
        """Update CamillaDSP data through API."""
        state: MediaPlayerState = MediaPlayerState.OFF
        volume: float = 0
        mute: bool = False
        source: str = ""
        source_list: list[str] = []
        capturerate: int = 0

        try:
            # Get state via GET /api/status?command=GetState
            statusData = json.loads(await self.async_get_api(endpoint="status", query={"command": "GetState"}))
            cdsp_state = statusData.get("value", "").lower()

            match cdsp_state:
                case "inactive":
                    state = MediaPlayerState.STANDBY
                case "paused":
                    state = MediaPlayerState.PAUSED
                case "running":
                    state = MediaPlayerState.PLAYING
                case "stalled":
                    state = MediaPlayerState.IDLE
                case "starting":
                    state = MediaPlayerState.ON
                case _:
                    state = MediaPlayerState.OFF

            if state != MediaPlayerState.OFF:
                # Get capture rate via GET /api/status?command=GetCaptureRate
                capturerate_data = json.loads(
                    await self.async_get_api(endpoint="status", query={"command": "GetCaptureRate"})
                )
                capturerate = int(capturerate_data.get("value", 0))

                # Get volume via GET /api/volume/get
                volume_data = json.loads(await self.async_get_api(endpoint="volume/get"))
                volume = float(volume_data.get("volume", 0))

                # Get mute via GET /api/mute/get
                mute_data = json.loads(await self.async_get_api(endpoint="mute/get"))
                mute = bool(mute_data.get("muted", False))

                # Get current config filepath via GET /api/config/filepath
                source_data = json.loads(await self.async_get_api(endpoint="config/filepath"))
                source = source_data.get("filePath", "")

                # Note: There is no "storedconfigs" endpoint in the CamillaDSP API.
                # source_list is left empty as there is no way to enumerate stored configs.
                source_list = []

        except ApiError:
            # Re-raise ApiError as-is (connection/API level errors)
            raise
        except json.JSONDecodeError as e:
            log = f"CamillaDSP error: failed to parse JSON response from {self.url}: {e}"
            LOGGER.warning(log)
            raise ApiError(f"Invalid JSON response from CamillaDSP: {e}") from e
        except Exception as e:
            log = f"CamillaDSP error: api call failed: {e}"
            LOGGER.warning(log)
            raise ApiError(f"API call failed: {e}") from e

        log = f"CamillaDSP update complete: state={state}, volume={volume}, source={source}"
        LOGGER.debug(log)

        return CDSPData(state=state,
                        volume=volume,
                        mute=mute,
                        source=source,
                        source_list=source_list,
                        capturerate=capturerate)

    async def async_get_api(self, endpoint: str, query: dict | None = None) -> Any:
        """Make a GET request to the CamillaDSP API."""
        url = f"{self.url}/api/{endpoint}"
        log = f"CamillaDSP GET {url}"
        LOGGER.debug(log)

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, params=query, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as res:
                if res.status != 200:
                    log = f"CamillaDSP API error: GET {url} returned status {res.status}"
                    LOGGER.warning(log)
                    raise ApiError(f"HTTP {res.status} from {url}")
                text = await res.text()
                LOGGER.debug(f"CamillaDSP GET {url} response: {text[:200]}")
                return text
        except ClientResponseError as e:
            log = f"CamillaDSP connection error: GET {url}: {e}"
            LOGGER.warning(log)
            raise ApiError(f"Connection error: {e}") from e
        except TimeoutError as e:
            log = f"CamillaDSP timeout: GET {url} timed out after {API_TIMEOUT}s"
            LOGGER.warning(log)
            raise ApiError(f"Request timed out: {e}") from e


    async def async_post_api(self, endpoint: str, data: dict) -> Any:
        """Make a POST request to the CamillaDSP API with JSON body."""
        url = f"{self.url}/api/{endpoint}"
        log = f"CamillaDSP POST {url} data={data}"
        LOGGER.debug(log)

        session = async_get_clientsession(self.hass)
        try:
            async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as res:
                if res.status != 200:
                    log = f"CamillaDSP API error: POST {url} returned status {res.status}"
                    LOGGER.warning(log)
                    raise ApiError(f"HTTP {res.status} from {url}")
                text = await res.text()
                LOGGER.debug(f"CamillaDSP POST {url} response: {text[:200]}")
                return text
        except ClientResponseError as e:
            log = f"CamillaDSP connection error: POST {url}: {e}"
            LOGGER.warning(log)
            raise ApiError(f"Connection error: {e}") from e
        except TimeoutError as e:
            log = f"CamillaDSP timeout: POST {url} timed out after {API_TIMEOUT}s"
            LOGGER.warning(log)
            raise ApiError(f"Request timed out: {e}") from e