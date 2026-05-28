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
        await self.async_post_api(endpoint="setparam/volume", data=str(volume))
        self._volume = volume

    async def async_set_muted(self, muted: bool):
        await self.async_post_api(endpoint="setparam/mute", data=str(muted))
        self._mute = muted

    async def async_select_source(self, source: str):
        data = f"{{\"name\":\"{source!s}\"}}"
        await self.async_post_api(endpoint="setactiveconfigfile", data=data)
        configData = await self.async_get_api(endpoint="getactiveconfigfile")
        if json.loads(configData)["configFileName"] == source:
            await self.async_post_api(endpoint="setconfig", data=configData)
            self._source = source
        else:
            LOGGER.warning("Error setting active config file")

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
            statusData = json.loads(await self.async_get_api(endpoint="status"))
            match statusData["cdsp_status"]:
                case 'INACTIVE':
                    state = MediaPlayerState.STANDBY
                case 'PAUSED':
                    state = MediaPlayerState.PAUSED
                case 'RUNNING':
                    state = MediaPlayerState.PLAYING
                case 'STALLED':
                    state = MediaPlayerState.IDLE
                case 'STARTING':
                    state = MediaPlayerState.ON

            if state != MediaPlayerState.OFF:
                if statusData.get("capturerate") is not None:
                    capturerate = statusData["capturerate"]
                else:
                    capturerate = 0

                volume = float(await self.async_get_api(endpoint="getparam/volume"))
                mute = (await self.async_get_api(endpoint="getparam/mute")) == "True"
                source = (json.loads(await self.async_get_api(endpoint="getactiveconfigfile"))["configFileName"])

                storedconfigs = json.loads(await self.async_get_api(endpoint="storedconfigs"))
                source_list = []
                for config in storedconfigs:
                    if config.get("name") is not None:
                        source_list.append(config.get("name"))

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

    async def async_get_api(self, endpoint: str) -> Any:
        """Make a GET request to the CamillaDSP API."""
        url = f"{self.url}/api/{endpoint}"
        log = f"CamillaDSP GET {url}"
        LOGGER.debug(log)

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as res:
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


    async def async_post_api(self, endpoint: str, data: str) -> Any:
        """Make a POST request to the CamillaDSP API."""
        url = f"{self.url}/api/{endpoint}"
        log = f"CamillaDSP POST {url} data={data}"
        LOGGER.debug(log)

        session = async_get_clientsession(self.hass)
        try:
            async with session.post(url, data=data, json=None, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as res:
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
