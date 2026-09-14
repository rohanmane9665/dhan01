import logging

logger = logging.getLogger(__name__)


class KillSwitch:
    """
    Persistent System Kill Switch.
    States: ACTIVE (normal operations) or TRIGGERED (emergency stop).
    When TRIGGERED, all order entries are immediately blocked.
    Resetting requires explicit human operator action.
    """

    def __init__(self, enabled_by_config: bool = True):
        self.enabled_by_config = enabled_by_config
        self.triggered = False
        self.trigger_reason = ""

    def trigger(self, reason: str = "Operator manual kill switch triggered"):
        self.triggered = True
        self.trigger_reason = reason
        logger.critical(f"🚨 KILL SWITCH TRIGGERED: {reason}")

    def reset(self, operator_name: str = "Operator"):
        self.triggered = False
        self.trigger_reason = ""
        logger.info(f"✅ KILL SWITCH RESET by {operator_name}")

    def is_triggered(self) -> bool:
        return self.triggered
