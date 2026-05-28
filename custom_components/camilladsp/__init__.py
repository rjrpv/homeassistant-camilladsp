from __future__ import annotations  # noqa: D104

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cdsp import ApiError, CDSPClient, InvalidUrl
from .const import CONFIG_URL, DOMAIN
from .coordinator import CDSPDataUpdateCoordinator

SCAN_INTERVAL = timedelta(seconds=10)

LOGGER = logging.getLogger(__name__)


# List of platforms to support. There should be a matching .py file for each,
# eg <cover.py> and <sensor.py>
PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    hass.data.setdefault(DOMAIN, {})

    url = entry.data[CONFIG_URL]

    log = f"CamillaDSP setting up entry for {url}"
    LOGGER.info(log)

    # Initialize connection to camilladsp
    cdsp = CDSPClient(hass, url)

    # Sanity check: validate connectivity before proceeding
    try:
        await cdsp.connect()
    except ApiError as ex:
        log = f"CamillaDSP unable to connect to {url}: {ex}"
        LOGGER.error(log)
        raise ConfigEntryNotReady(f"Error while communicating to CamillaDSP at {url}: {ex}") from ex
    except InvalidUrl as ex:
        log = f"CamillaDSP invalid URL {url}: {ex}"
        LOGGER.error(log)
        raise ConfigEntryNotReady(f"Invalid CamillaDSP URL: {ex}") from ex
    except Exception as ex:
        log = f"CamillaDSP unexpected error during setup for {url}: {type(ex).__name__}: {ex}"
        LOGGER.error(log, exc_info=True)
        raise ConfigEntryNotReady(f"Unexpected error during setup: {type(ex).__name__}: {ex}") from ex

    if not cdsp.connected:
        log = f"CamillaDSP client is not connected to {url} after setup"
        LOGGER.error(log)
        raise ConfigEntryNotReady(f"CamillaDSP client is not connected to {url}")

    log = f"CamillaDSP connected to {url}, proceeding with setup"
    LOGGER.info(log)

    log = f"CamillaDSP entry: {entry}"
    LOGGER.debug(log)

    coordinator = CDSPDataUpdateCoordinator(hass, cdsp, SCAN_INTERVAL)  # type: ignore[arg-type]
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_update_listener))

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

async def _update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
