import pytest
from datetime import datetime
from app.risk.manager import RiskManager
from app.strategies.models import Signal

def test_kill_switch():
    rm = RiskManager()
    signal = Signal(
        strategy_id="test", symbol="test CE", security_id="123", option_type="CE",
        direction="BUY", quantity=10, entry_reference=100.0, stop_loss_reference=90.0,
        signal_reason="test pattern", timestamp=datetime.now()
    )
    
    # Pre-condition
    assert rm.evaluate_signal(signal)[0] == True
    
    # Trigger kill switch
    rm.kill_switch.trigger()
    assert rm.evaluate_signal(signal)[0] == False
    
def test_max_daily_loss():
    rm = RiskManager()
    rm.current_daily_pnl = -5001.0
    
    signal = Signal(
        strategy_id="test", symbol="test CE", security_id="123", option_type="CE",
        direction="BUY", quantity=10, entry_reference=100.0, stop_loss_reference=90.0,
        signal_reason="test pattern", timestamp=datetime.now()
    )
    
    # Assuming max daily loss is configured to 5000 in settings
    rm.max_daily_loss = 5000.0
    assert rm.evaluate_signal(signal)[0] == False
