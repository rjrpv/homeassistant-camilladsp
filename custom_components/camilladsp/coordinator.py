import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cdsp import ApiError, CDSPClient
from .const import DOMAIN
from .model import CDSPData

LOGGER = logging.getLogger(__name__)


class CDSPDataUpdateCoordinator(DataUpdateCoordinator[CDSPData]):  # type: ignore[misc]
    """Class to manage fetching CamillaDSP data from single endpoint."""

    def __init__(self, hass: HomeAssistant, cdsp: CDSPClient, interval: float) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, LOGGER, name=DOMAIN, update_interval=interval)
        self.cdsp = cdsp

    async def _async_update_data(self) -> CDSPData:
        if self.hass.is_stopping:
            raise UpdateFailed("Home Assistant is stopping")

        try:
            return await self.cdsp.update()

        except ApiError as err:
            LOGGER.error("CamillaDSP API error during data update: %s", err)
            raise UpdateFailed(f"Error communicating with CamillaDSP API: {err}") from err
        except Exception as err:
            LOGGER.error("CamillaDSP unexpected error during data update: %s: %s", type(err).__name__, err, exc_info=True)
            raise UpdateFailed(f"Unexpected error from CamillaDSP: {type(err).__name__}: {err}") from err
