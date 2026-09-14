from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BrokerAdapter(ABC):
    """
    Abstract Interface for Broker Operations (PAPER / LIVE).
    Concrete implementations will be built in subsequent migration phases.
    """

    @abstractmethod
    async def connect(self) -> bool:
        """Establishes connection to the broker / paper engine."""
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """Gracefully disconnects from broker."""
        pass

    @abstractmethod
    async def place_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Places a new order."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancels an existing order."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Retrieves active positions from broker."""
        pass

    @abstractmethod
    async def get_orders(self) -> List[Dict[str, Any]]:
        """Retrieves order history from broker."""
        pass
