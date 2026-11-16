from __future__ import annotations

from abc import ABC, abstractmethod

from dfhack_bridge.protocol import FortressState


class BridgeError(RuntimeError):
    """Raised when the backend is unreachable or rejects a command."""


class FortressBridge(ABC):
    """Everything the autopilot and the GUI may read from / do to the game."""

    name: str = "bridge"
    # Action keys (e.g. "act.expand_stockpile") this backend can't perform; the autopilot skips them.
    unsupported: frozenset[str] = frozenset()

    @abstractmethod
    def get_state(self) -> FortressState: ...

    @abstractmethod
    def create_work_order(self, job: str, quantity: int, *, source: str = "autopilot") -> int:
        """Create a work order and return its id."""

    @abstractmethod
    def cancel_work_order(self, order_id: int) -> None: ...

    @abstractmethod
    def expand_stockpile(self, stockpile_id: int, cells: int) -> None: ...

    @abstractmethod
    def link_stockpile(self, stockpile_id: int, workshop: str) -> None: ...

    @abstractmethod
    def enqueue_blueprint(self, queue_id: str, blueprint: str, zone_id: str) -> None:
        """Queue a Quickfort blueprint inside a zone designated by the user."""

    def advance_blueprint(self, queue_id: str) -> None:
        """Apply the build/place phase of a blueprint whose digging is finished (BlueprintJob.awaiting_build)."""
        raise BridgeError(f"{self.name} advances blueprints by itself")

    @abstractmethod
    def set_paused(self, paused: bool) -> None: ...

    def close(self) -> None:
        """Release resources. Optional."""
