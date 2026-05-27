from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator import CDSPDataUpdateCoordinator
from .model import CDSPData


class CDSPEntity(CoordinatorEntity[CDSPData]):  # type:ignore [misc]
    """Represent a CDSP Entity."""

    coordinator: CDSPDataUpdateCoordinator

    def __init__(
        self,
        coordinator: CDSPDataUpdateCoordinator,
    ) -> None:
        """Initialise the gateway."""
        super().__init__(coordinator)
