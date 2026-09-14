import time, json, urllib.request, csv
import pandas as pd
import pyotp
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from datetime import datetime, timedelta
from collections import deque
import threading
import math
import requests
import pytz # Import pytz for timezone awareness

from datetime import datetime, timedelta
import pytz # Make sure this import is at the top of your file if not already there

# Define IST timezone once

# === Configuration Constants ===
BREAKOUT_WINDOW_SECONDS = 900 # 15 minutes * 60 seconds

# Add this line near your other global configuration variables (e.g., below token, before initialize SmartAPI)
IST = pytz.timezone('Asia/Kolkata')

current_minute_data = {} # Make sure this global variable exists and is accessible

def validate_websocket_data():
    """Enhanced WebSocket data validation with diagnostics"""
    global current_minute_data

    # Check if current_minute_data exists
    if not current_minute_data:
        print("⚠️ WARNING: current_minute_data is not initialized")
        return False

    # Check timestamp
    if current_minute_data.get('timestamp') is None:
        print("⚠️ WARNING: No timestamp in current_minute_data")
        return False

    # Check if timestamp is recent
    last_update = current_minute_data['timestamp']
    current_time_for_validation = datetime.now(IST).replace(tzinfo=None)
    time_diff = (current_time_for_validation - last_update).total_seconds()

    if time_diff > 300:  # More than 5 minutes old
        print(f"⚠️ WARNING: Last data is {time_diff:.0f} seconds old")
        return False
    
    # Check if we have actual price data
    if current_minute_data.get('close') is None or current_minute_data.get('close') <= 0:
        print("⚠️ WARNING: No valid price data received")
        return False

    return True

# === IMPORTANT NOTE ON CHANGES ===
# The core logic for detecting the pattern and the immediate breakout has been refined.
# - 'b1' and 'b2' are explicitly the last two *completed* 5-minute candles.
# - A new variable `pattern_formation_time` tracks when the B1/B2 pattern completes.
# - The 'buy PE' condition (current Sensex LTP breaking `b1['low']`) now only triggers IF
#   the pattern was just formed AND the breakout occurs within 15 minutes of that pattern formation.
# - No other parts of your existing code (credentials, login, helper functions, etc.) have been touched.
# === END NOTE ON CHANGES ===

# === Credentials ===
api_key = "XezA2pz7"
username = "M464983"
pwd = "1980"
token = "FQO6KWBEHR3PYGV4UFPHFIYAFY"

# === Initialize SmartAPI ===
smartApi = SmartConnect(api_key)
totp = pyotp.TOTP(token).now()
login_data = smartApi.generateSession(username, pwd, totp)

if not login_data['status']:
    print("Login failed:", login_data)
    exit()

feed_token = smartApi.getfeedToken()
authToken = login_data['data']['jwtToken']

print("✅ Login Successful!")
print("=" * 50)

# === Global Variables ===
instruments_df = None
sensex_token = None
current_position = None  # Store position details for target/SL monitoring
trade_log = []  # Store trade history
daily_pnl = 0 # New: Track cumulative P&L for the day

# === Timezone Definition ===
IST = pytz.timezone('Asia/Kolkata')

# Add this after IST definition:
def make_timezone_naive(dt):
    """Convert timezone-aware datetime to naive datetime in IST"""
    if dt.tzinfo is not None:
        return dt.astimezone(IST).replace(tzinfo=None)
    return dt

# === Time Management ===
# Ensure these times are timezone-aware if comparing with datetime.now(IST)
ENTRY_CUTOFF_HOUR = 15
ENTRY_CUTOFF_MINUTE = 10
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
AUTO_SQUARE_OFF_HOUR = 15 # New: For explicit square-off
AUTO_SQUARE_OFF_MINUTE = 25 # New: For explicit square-off

ENTRY_CUTOFF_TIME = datetime.now(IST).replace(hour=ENTRY_CUTOFF_HOUR, minute=ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
MARKET_CLOSE_TIME = datetime.now(IST).replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
AUTO_SQUARE_OFF_TIME = datetime.now(IST).replace(hour=AUTO_SQUARE_OFF_HOUR, minute=AUTO_SQUARE_OFF_MINUTE, second=0, microsecond=0) # New

# New global variable to track pattern formation time
pattern_formation_time = None

# === WebSocket Reconnection Variables ===
reconnect_attempts = 0
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_SECONDS = 10 # Delay between reconnect attempts

# === Auto Square-off Scheduling Variable ===
square_off_scheduled = False

# === Trading System Control ===
TRADE_ACTIVE = True # Global flag to control if trading is allowed
MAX_DAILY_LOSS = 1500 # New: Maximum allowed daily loss

def is_entry_allowed():
    """Check if new entries are allowed (before ENTRY_CUTOFF_TIME)"""
    current_time = datetime.now(IST).time()
    cutoff_time = ENTRY_CUTOFF_TIME.time()
    
    entry_allowed = current_time < cutoff_time
    if not entry_allowed:
        print(f"⏰ No new entries allowed after {ENTRY_CUTOFF_TIME.strftime('%H:%M %p')} (Current: {current_time.strftime('%H:%M:%S')})")
    
    return entry_allowed

def is_market_closing_soon():
    """Check if market is closing soon (within 5 minutes of MARKET_CLOSE_TIME)"""
    current_time = datetime.now(IST)
    time_to_close = MARKET_CLOSE_TIME - current_time
    
    return time_to_close.total_seconds() <= 300  # 5 minutes

def get_order_status(order_id):
    """Get order status from Angel One API"""
    try:
        order_book = smartApi.orderBook()
        if order_book['status']:
            for order in order_book['data']:
                if order['orderid'] == order_id:
                    return order['orderstatus'], float(order.get('averageprice', 0))
        return None, 0
    except Exception as e:
        print(f"❌ Error getting order status: {e}")
        return None, 0

def wait_for_order_execution(order_id, max_wait_time=60):
    """Wait for order execution and return execution details"""
    print(f"⏳ Waiting for order execution... (Order ID: {order_id})")
    
    start_time = time.time()
    while time.time() - start_time < max_wait_time:
        status, avg_price = get_order_status(order_id)
        
        if status == "COMPLETE":
            print(f"✅ Order EXECUTED at ₹{avg_price:.2f}")
            return True, avg_price
        elif status in ["REJECTED", "CANCELLED"]:
            print(f"❌ Order {status}")
            return False, 0
        
        time.sleep(2)  # Check every 2 seconds
    
    print(f"⏰ Order execution timeout after {max_wait_time} seconds")
    return False, 0

# === Position Management Class ===
class Position:
    def __init__(self, symbol, token, entry_price, quantity, order_id):
        self.symbol = symbol
        self.token = token
        self.entry_price = entry_price
        self.quantity = quantity
        self.order_id = order_id
        self.target_price = entry_price + 25  # ₹25 target
        self.stop_loss_price = entry_price - 20  # ₹20 stop loss
        self.entry_time = datetime.now(IST).replace(tzinfo=None) # Use IST, then make naive for consistency
        self.is_active = True
        self.exit_order_id = None
        
        print(f"🎯 POSITION CREATED:")
        print(f"   Symbol: {self.symbol}")
        print(f"   Entry Price: ₹{self.entry_price:.2f}")
        print(f"   Target: ₹{self.target_price:.2f} (+₹25)")
        print(f"   Stop Loss: ₹{self.stop_loss_price:.2f} (-₹20)")
        print(f"   Quantity: {self.quantity}")
        print(f"   Entry Time: {self.entry_time.strftime('%Y-%m-%d %H:%M:%S')}")

    def check_exit_conditions(self, current_ltp):
        """Check if target or stop loss is hit"""
        if not self.is_active:
            return None
        
        # Force exit if market is closing soon - this is a continuous check,
        # the scheduled square-off is a precise one-time event.
        if is_market_closing_soon():
            print(f"🕒 MARKET CLOSING SOON! Forcing exit at current price: ₹{current_ltp:.2f}")
            return "MARKET_CLOSE"
            
        # Check target/SL conditions
        if current_ltp >= self.target_price:
            print(f"🎯 TARGET HIT! Current: ₹{current_ltp:.2f} | Target: ₹{self.target_price:.2f}")
            return "TARGET"
        elif current_ltp <= self.stop_loss_price:
            print(f"🛑 STOP LOSS HIT! Current: ₹{current_ltp:.2f} | SL: ₹{self.stop_loss_price:.2f}")
            return "STOP_LOSS"
        
        return None

# === Load Instruments Master Data ===
def load_instruments_data():
    """Load instrument master data from Angel One API with improved SENSEX token detection"""
    global instruments_df, sensex_token
    
    try:
        print("📋 Loading Instruments Master Data...")
        url = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
        
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            instruments_data = response.json()
            instruments_df = pd.DataFrame(instruments_data)
            
            # IMPROVED: Try multiple variations to find SENSEX token
            print("🔍 Searching for SENSEX token...")
            
            # Method 1: Try BSE SENSEX
            sensex_candidates = instruments_df[
                (instruments_df['name'].str.contains('SENSEX', case=False, na=False)) |
                (instruments_df['symbol'].str.contains('SENSEX', case=False, na=False)) |
                (instruments_df['tradingsymbol'].str.contains('SENSEX', case=False, na=False))
            ]
            
            print(f"Found {len(sensex_candidates)} SENSEX-related instruments:")
            for idx, row in sensex_candidates.iterrows():
                print(f"  - {row['name']} | {row['symbol']} | {row['tradingsymbol']} | Token: {row['token']} | Exchange: {row['exch_seg']}")
            
            # Priority order: BSE first, then NSE
            for exchange in ['BSE', 'NSE']:
                sensex_match = sensex_candidates[sensex_candidates['exch_seg'] == exchange]
                if not sensex_match.empty:
                    sensex_token = sensex_match.iloc[0]['token']
                    print(f"✅ SENSEX Token Selected: {sensex_token} ({exchange})")
                    break
            
            # If still not found, try known working tokens
            if sensex_token is None:
                print("⚠️ SENSEX token not found in instruments. Trying known working tokens...")
                # Common SENSEX tokens used by different brokers
                test_tokens = ["99919000", "1"]
                for test_token in test_tokens:
                    print(f"Testing token: {test_token}")
                    test_ltp = get_ltp_data(test_token, "BSE")
                    if test_ltp and test_ltp > 1000:  # SENSEX is typically > 1000
                        sensex_token = test_token
                        print(f"✅ Working SENSEX Token Found: {sensex_token} (LTP: {test_ltp})")
                        break
            
            if sensex_token is None:
                print("❌ Could not determine SENSEX token. Please check manually.")
                return False
                
            print(f"📊 Loaded {len(instruments_df)} instruments")
            return True
            
    except Exception as e:
        print(f"❌ Error loading instruments: {e}")
        return False

def find_option_token(strike_price, option_type='PE', expiry_date=None):
    """
    Fully fixed version to find Angel One SENSEX option token.
    - Uses exch_seg = 'BFO'
    - Matches strike * 100
    - Finds nearest expiry
    """
    global instruments_df

    if instruments_df is None or instruments_df.empty:
        print("❌ Instruments data not loaded.")
        return None, None

    try:
        strike_int = int(strike_price)
        strike_raw = strike_int * 100  # Angel One stores 83500 as 8350000

        df = instruments_df.copy()

        # Force types and uppercase
        df['symbol'] = df['symbol'].astype(str).str.upper()
        df['name'] = df['name'].astype(str).str.upper()
        df['instrumenttype'] = df['instrumenttype'].astype(str).str.upper()
        df['exch_seg'] = df['exch_seg'].astype(str).str.upper()
        df['strike'] = df['strike'].astype(float)
        df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')

        today = datetime.now()
        # Filter for matching PE options
        filtered = df[
            (df['name'] == 'SENSEX') &
            (df['instrumenttype'] == 'OPTIDX') &
            (df['exch_seg'] == 'BFO') &
            (df['strike'] == strike_raw) &
            (df['symbol'].str.endswith(option_type)) &
            (df['expiry'] >= today)
        ]

        if filtered.empty:
            print(f"❌ No SENSEX {option_type} option found for strike {strike_price}.")
            return None, None

        # Pick the nearest expiry
        filtered = filtered.sort_values('expiry')
        selected = filtered.iloc[0]
        print(f"✅ Found Option: {selected['symbol']} | Token: {selected['token']} | Expiry: {selected['expiry'].strftime('%d-%b-%Y')}")

        return selected['token'], selected['symbol']

    except Exception as e:
        print(f"❌ Error in find_option_token: {e}")
        return None, None


def get_ltp_data(token, exchange="BSE"):
    """Get LTP data using Angel One API"""
    try:
        ltp_data = smartApi.ltpData(exchange, token, token)
        if ltp_data['status']:
            return float(ltp_data['data']['ltp'])
        else:
            print(f"❌ Failed to get LTP for token {token}: {ltp_data.get('message', 'Unknown error')}")
            return None
    except Exception as e:
        print(f"❌ Error getting LTP for token {token}: {e}")
        return None

# === Account Balance Functions ===
def get_account_balance():
    try:
        rms_data = smartApi.rmsLimit()
        if rms_data['status']:
            balance_info = rms_data['data']
            print("💰 ACCOUNT BALANCE DETAILS:")
            print("-" * 30)
            print(f"Available Cash: ₹{balance_info.get('availablecash', 'N/A')}")
            print(f"Available Margin: ₹{balance_info.get('availablemargin', 'N/A')}")
            print(f"Collateral: ₹{balance_info.get('collateral', 'N/A')}")
            print(f"M2M Realized: ₹{balance_info.get('m2mrealized', 'N/A')}")
            print(f"M2M Unrealized: ₹{balance_info.get('m2munrealized', 'N/A')}")
            print("-" * 30)
            return float(balance_info.get('availablecash', 0))
        else:
            print("❌ Failed to fetch balance:", rms_data.get('message', 'Unknown error'))
            return 0
    except Exception as e:
        print(f"❌ Error fetching balance: {e}")
        return 0

# Get user profile (using try-except for robustness)
try:
    getbalance = smartApi.getProfile(login_data['data']['refreshToken'])
    if getbalance['status']:
        profile = getbalance['data']
        print("👤 USER PROFILE:")
        print(f"Name: {profile.get('name', 'N/A')}")
        print(f"Email: {profile.get('email', 'N/A')}")
        print(f"Mobile: {profile.get('mobileno', 'N/A')}")
        print(f"Broker: {profile.get('broker', 'N/A')}")
        print("=" * 50)
    else:
        print(f"❌ Failed to get profile: {getbalance.get('message', 'Unknown error')}")
except Exception as e:
    print(f"❌ Error fetching profile: {e}")

# Load instruments data first
load_instruments_data()

available_balance = get_account_balance()
print()

# === Trading Configuration ===
EXCHANGE = "BSE"  # For SENSEX index (assuming BSE token for SENSEX index now)
OPTION_EXCHANGE = "BSE"  # For SENSEX options

# Trading Configuration
POSITION_SIZE = 1 # Number of lots
MAX_TRADE_AMOUNT_PERCENT = 0.1 # Max 10% of available cash
MAX_TRADE_AMOUNT_CAP = 10000 # Max ₹10,000 per trade
TARGET_POINTS = 25  # ₹25 target
STOP_LOSS_POINTS = 20 # ₹20 stop loss
MIN_TIME_BETWEEN_TRADES_SECONDS = 300 # 5 minutes cooldown between trade attempts

# Store 5-minute candle data
candle_data = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
current_minute_data = {
    'open': None, 'high': None, 'low': None, 'close': None, 'volume': 0, 'timestamp': None
}

# Trading state
trade_executed = False # Renamed from `trade_executed` to reflect if a trade is in progress
last_trade_attempt_time = None # To enforce cooldown period for new trade attempts

def get_5min_timestamp():
    """Get the current 5-minute interval timestamp (timezone-naive IST)"""
    now = datetime.now()  # Remove IST here - keep it naive
    minute = (now.minute // 5) * 5
    return now.replace(minute=minute, second=0, microsecond=0)

def get_current_expiry():
    """
    Get current month SENSEX options expiry date (last Thursday of the month).
    If current date is past the current month's expiry, return next month's expiry.
    """
    now = datetime.now(IST)

    def find_last_thursday(year, month):
        # Go to the first day of the next month
        if month == 12:
            next_month_first_day = datetime(year + 1, 1, 1, tzinfo=IST)
        else:
            next_month_first_day = datetime(year, month + 1, 1, tzinfo=IST)
        # Subtract one day to get the last day of the current month
        last_day_of_month = next_month_first_day - timedelta(days=1)
        # Calculate days to go back to find the last Thursday (Thursday is weekday 3)
        days_since_last_thursday = (last_day_of_month.weekday() - 3 + 7) % 7
        return last_day_of_month - timedelta(days=days_since_last_thursday)

    expiry_date = find_last_thursday(now.year, now.month)
    # If the current date is past the calculated expiry for this month, get next month's expiry
    if expiry_date < now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=IST):
        next_month = now.month + 1
        next_year = now.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        expiry_date = find_last_thursday(next_year, next_month)

    # Format as DDMMMYY (e.g., 27JUN24)
    return expiry_date.strftime('%d%b%y').upper()


def get_atm_strike(current_price):
    """Get At-The-Money strike price for SENSEX options (nearest 100)"""
    strike = round(current_price / 100) * 100
    return strike

def place_option_order(option_token, option_symbol, transaction_type="BUY", quantity=1):
    """Place market order for SENSEX PE option with execution confirmation"""
    try:
        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": option_symbol,
            "symboltoken": option_token,
            "transactiontype": transaction_type,
            "exchange": OPTION_EXCHANGE,
            "ordertype": "MARKET", # Placing Market order to ensure immediate execution on trigger
            "producttype": "NRML", # Normal product type
            "duration": "DAY",
            "quantity": str(quantity) # Quantity must be a string
        }
        
        print(f"🔥 PLACING {transaction_type} ORDER: {option_symbol} (Qty: {quantity})")
        print(f"📋 Order Details: {order_params}")
        
        order_response = smartApi.placeOrder(order_params)
        
        if order_response['status']:
            order_id = order_response['data']['orderid']
            print(f"✅ ORDER PLACED SUCCESSFULLY! Order ID: {order_id}")
            # Wait for the order to actually be filled
            executed, avg_price = wait_for_order_execution(order_id)
            if executed:
                return order_id, avg_price
            else:
                print(f"❌ Order {order_id} not executed properly or timed out. (Status: {get_order_status(order_id)[0]})")
                return None, 0
        else:
            print(f"❌ ORDER FAILED: {order_response.get('message', 'Unknown error')} | Order Params: {order_params}")
            return None, 0
            
    except Exception as e:
        print(f"❌ Critical error placing order for {option_symbol}: {e}")
        return None, 0

def exit_position(position, exit_reason):
    """Exit current position with execution confirmation"""
    global current_position, trade_log, daily_pnl, TRADE_ACTIVE
    
    if position is None or not position.is_active:
        print(f"Attempted to exit a non-existent or inactive position for reason: {exit_reason}.")
        return False

    try:
        print(f"\n🚪 EXITING POSITION - Reason: {exit_reason}")
        print(f"🕒 Exit Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}") # Use IST
        
        # Place a SELL order to close the BUY PE position
        exit_order_id, exit_price = place_option_order(
            position.token, 
            position.symbol, 
            "SELL", # Transaction type is SELL to close a BUY position
            position.quantity
        )
        
        if exit_order_id and exit_price > 0:
            position.is_active = False
            position.exit_order_id = exit_order_id
            
            pnl = (exit_price - position.entry_price) * position.quantity
            daily_pnl += pnl # Update daily P&L
            
            print(f"💰 TRADE COMPLETED!")
            print(f"  Entry Price: ₹{position.entry_price:.2f}")
            print(f"  Exit Price: ₹{exit_price:.2f}")
            print(f"  P&L: ₹{pnl:.2f}")
            print(f"  Daily P&L: ₹{daily_pnl:.2f}") # New: Display daily P&L
            print(f"  Duration: {datetime.now(IST) - position.entry_time}") # Use IST
            
            trade_log.append({
           'symbol': position.symbol,
           'entry_time': position.entry_time.strftime('%Y-%m-%d %H:%M:%S'),
           'exit_time': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'), # Use IST
           'entry_price': position.entry_price,
           'exit_price': exit_price,
           'quantity': position.quantity,
           'pnl': pnl,
           'exit_reason': exit_reason
           })
            current_position = None # Clear the active position

            # New: Check for max daily loss after each trade
            if daily_pnl <= -MAX_DAILY_LOSS:
                TRADE_ACTIVE = False
                print(f"🛑🛑 MAX DAILY LOSS OF ₹{MAX_DAILY_LOSS} REACHED! No more trades will be placed today. 🛑🛑")

            return True
        else:
            print(f"❌ Failed to exit position for {position.symbol}. Position might still be open!")
            return False
            
    except Exception as e:
        print(f"❌ Error exiting position: {e}")
        return False

def monitor_position():
    """
    Monitor the active position (if any) for target, stop-loss, or market close conditions.
    This function will be called periodically by the position_monitor_thread.
    """
    global current_position

    if current_position and current_position.is_active:
        # Get the latest LTP for the held option contract
        current_option_ltp = get_ltp_data(current_position.token, OPTION_EXCHANGE)
        
        if current_option_ltp is not None:
            # Check exit conditions based on the latest LTP
            exit_reason = current_position.check_exit_conditions(current_option_ltp)
            
            if exit_reason:
                # If an exit condition is met, exit the position
                exit_position(current_position, exit_reason)
        # else:
            # print(f"⚠️ Could not get LTP for active position {current_position.symbol}. Retrying...")
    # else:
        # print("No active position to monitor.") # Commented to reduce excessive logs
def fallback_data_feed():
    """Fallback data feed using REST API calls when WebSocket fails"""
    global sensex_token, current_minute_data
    
    print("🔄 Starting fallback data feed via REST API...")
    
    while True:
        try:
            # Get current LTP
            ltp = get_ltp_data(sensex_token, "BSE")
            
            if ltp and ltp > 0:
                now_naive_ist = datetime.now(IST).replace(tzinfo=None)
                
                # THE BUGGY LINE HAS BEEN REMOVED.
                # Let update_5min_candle handle the logic.
                
                update_5min_candle(ltp, 0)  # Volume = 0 for REST API
                print(f"🔄 Fallback Data: ₹{ltp:.2f} at {now_naive_ist.strftime('%H:%M:%S')}")
            else:
                print("❌ Failed to get LTP via REST API")
            
            time.sleep(5)  # Update every 5 seconds
            
        except Exception as e:
            print(f"❌ Error in fallback data feed: {e}")
            time.sleep(10)

def monitor_position_loop():
    """Enhanced position monitoring with better error handling"""
    print("🔍 Starting position monitor...")
    time.sleep(10)  # Give WebSocket time to connect
    
    fallback_started = False
    
    while True:
        try:
            # Check WebSocket data validity
            if not validate_websocket_data():
                print("🚨 WebSocket data validation failed!")
                
                if not fallback_started:
                    print("🔄 Starting fallback data feed...")
                    fallback_thread = threading.Thread(target=fallback_data_feed)
                    fallback_thread.daemon = True
                    fallback_thread.start()
                    fallback_started = True
            
            # Check market timing
            current_time_ist = datetime.now(IST).time()
            market_close_time = MARKET_CLOSE_TIME.time()
            
            if current_time_ist >= market_close_time:
                if current_position and current_position.is_active:
                    print("🕒 Market closing - forcing position exit")
                    exit_position(current_position, "MARKET_CLOSE")
                print("📈 Market closed. Stopping monitor.")
                break
            
            # Monitor active position
            monitor_position()
            
            time.sleep(5)  # Check every 5 seconds
            
        except Exception as e:
            print(f"❌ Error in position monitor: {e}")
            time.sleep(10)

def check_trading_conditions(current_sensex_ltp):
    global last_trade_attempt_time, candle_data, current_position, pattern_formation_time, TRADE_ACTIVE, b1, b2 # MOVED THIS LINE UP!
    
    # Add these lines for debugging:
    print(f"🔍 DEBUG - Current position exists: {current_position is not None}")
    print(f"🔍 DEBUG - Current position active: {current_position.is_active if current_position else 'N/A'}")
    print(f"🔍 DEBUG - TRADE_ACTIVE flag: {TRADE_ACTIVE}")
    print(f"🔍 DEBUG - Pattern formation time: {pattern_formation_time}")
    # End of debug lines

    """
    Check if trading conditions are met and execute trade if needed.
    This function implements the "B1 (green), B2 (red), RUNNING CANDLE breaks B1 low" logic.
    """
    
    # --- RULE 0: Check if trading is globally active (e.g., not stopped due to max daily loss) ---
    if not TRADE_ACTIVE:
        # print("⛔ Trading is inactive due to max daily loss or other conditions.") # Commented to reduce excessive logs
        return

    # --- RULE 1: Only one open position at a time ---
    if current_position and current_position.is_active:
        # print("➡️ Skipping new trade signal: A position is already active.") # Commented to reduce excessive logs
        return

    # --- Rule: Check if new entries are allowed (before ENTRY_CUTOFF_TIME) ---
    if not is_entry_allowed():
        return
    
    # --- RULE 2: Cool down period between trade attempts ---
    if last_trade_attempt_time and (datetime.now(IST) - last_trade_attempt_time).total_seconds() < MIN_TIME_BETWEEN_TRADES_SECONDS:
        print(f"⏳ Trade cooling down. Last attempt { (datetime.now(IST) - last_trade_attempt_time).total_seconds():.0f}s ago. Need {MIN_TIME_BETWEEN_TRADES_SECONDS}s.")
        return
    
    # --- RULE 3: Ensure enough historical (completed) candles are available for pattern detection ---
    # We need at least 2 completed candles for the pattern (B1, B2).
    # The current running candle will be implicitly the 'B3' for the breakout.
    if len(candle_data) < 2:
        # print(f"Not enough completed candles ({len(candle_data)} < 2) to check conditions.") # Commented to reduce excessive logs
        return
    
    # --- Identify B1 and B2 (both are COMPLETED candles) ---
    # B1 is the second to last completed candle, B2 is the last completed candle.
    b1 = candle_data.iloc[-2] # This is the 'green' candle of your pattern
    b2 = candle_data.iloc[-1] # This is the 'red' candle of your pattern
    # The currently forming candle is implicitly 'B3' and its current price is `current_sensex_ltp`.

    print(f"\n🔍 CHECKING CONDITIONS at {datetime.now(IST).strftime('%H:%M:%S')} (Current SENSEX LTP: ₹{current_sensex_ltp:.2f}):")
    print(f"B1 (Completed @{b1['timestamp'].strftime('%H:%M')}): O:{b1['open']:.2f} H:{b1['high']:.2f} L:{b1['low']:.2f} C:{b1['close']:.2f}")
    print(f"B2 (Completed @{b2['timestamp'].strftime('%H:%M')}): O:{b2['open']:.2f} H:{b2['high']:.2f} L:{b2['low']:.2f} C:{b2['close']:.2f}")
    print(f"B3 (Running Candle - current LTP): ₹{current_sensex_ltp:.2f}") # This represents the live status of your 'B3'
    
    # --- Condition 1: B1 must be bullish (close > open) ---
    condition1 = b1['close'] > b1['open']
    print(f"C1 (B1 Bullish): {condition1} ({b1['close']:.2f} > {b1['open']:.2f})")
    
    # --- Condition 2: B2 must be bearish (close < open) ---
    condition2 = b2['close'] < b2['open']
    print(f"C2 (B2 Bearish): {condition2} ({b2['close']:.2f} < {b2['open']:.2f})")
    
    # --- Condition 3: B1 high < B2 high (B1 engulfed by B2 on upside) ---
    condition3 = b1['high'] < b2['high']
    print(f"C3 (B1 High < B2 High): {condition3} ({b1['high']:.2f} < {b2['high']:.2f})")
    
    # --- Condition 4: B1 low > B2 low (B1 engulfed by B2 on downside) ---
    condition4 = b1['low'] < b2['low'] 
    print(f"C4 (B1 Low > B2 Low): {condition4} ({b1['low']:.2f} > {b2['low']:.2f})")
    
    # --- Check the first four conditions to identify the pattern formation (B1 & B2 completion) ---
    if condition1 and condition2 and condition3 and condition4:
        # Pattern (B1, B2) identified. Record its completion time (the timestamp of B2).
        # This is the point from which your 15-minute breakout window starts.
        if pattern_formation_time is None or pattern_formation_time != b2['timestamp']:
            pattern_formation_time = b2['timestamp']
            print(f"✅ Pattern (B1, B2) met. Breakout window starts from: {pattern_formation_time.strftime('%H:%M:%S')}")
        
        # --- Condition 5: Breakout of green candle (B1) low by the running candle (implicit B3) within 15 minutes ---
        if pattern_formation_time: # Ensure pattern_formation_time has been set
            # Make both timestamps timezone-naive for consistent comparison
            current_time_naive = datetime.now(IST).replace(tzinfo=None)
            pattern_time_naive = make_timezone_naive(pattern_formation_time) if pattern_formation_time.tzinfo else pattern_formation_time

            time_since_pattern_formation = (current_time_naive - pattern_time_naive).total_seconds()
            breakout_window_active = time_since_pattern_formation <= BREAKOUT_WINDOW_SECONDS # 15 minutes = 900 seconds
            print(f"Time since pattern (B2 close): {time_since_pattern_formation:.0f}s (Max {BREAKOUT_WINDOW_SECONDS}s allowed)")

            if breakout_window_active:
                # This is the core of your "B3 is running candle breaks B1 low immediately" logic.
                # `current_sensex_ltp` is the LIVE PRICE of the currently forming candle.
                condition5 = current_sensex_ltp < b1['low']
                print(f"C5 (Running Candle LTP < B1 Low): {condition5} ({current_sensex_ltp:.2f} < {b1['low']:.2f})")

                # --- START OF CONDITION SUMMARY ---
                print(f"\n🔍 CONDITION SUMMARY:")
                print(f"C1 (B1 Bullish): {condition1}")
                print(f"C2 (B2 Bearish): {condition2}") 
                print(f"C3 (B1H < B2H): {condition3}")
                print(f"C4 (B1L > B2L): {condition4}")
                print(f"C5 (Breakout): {condition5}") # condition5 is defined here
                print(f"Pattern formed: {pattern_formation_time is not None}")
                print(f"Within window: {breakout_window_active}") # breakout_window_active is defined here
                print(f"No position: {current_position is None}")
                print(f"Trading active: {TRADE_ACTIVE}")
                # --- END OF CONDITION SUMMARY ---


                if condition5 and TRADE_ACTIVE and not current_position: # Ensure no active position AND trading is active
                    print("\n🚨 ALL CONDITIONS MET! EXECUTING TRADE...")

                    # --- Trade Execution ---
                    # 1. Determine ATM strike based on current live Sensex price
                    atm_strike = get_atm_strike(current_sensex_ltp)
                    print(f"🎯 Target ATM Strike for PE: {atm_strike}")
                    print(f"📈 Current SENSEX LTP used for strike calculation: {current_sensex_ltp:.2f}")

                    # 2. Find the specific option token
                    option_token, option_symbol = find_option_token(atm_strike, 'PE')

                    if option_token and option_symbol:
                        # --- START OF NEW TRADE EXECUTION VALIDATION PRINTS ---
                        print(f"\n🚨 ATTEMPTING TRADE EXECUTION:")
                        print(f"   - Current SENSEX LTP: {current_sensex_ltp:.2f}")
                        print(f"   - ATM Strike: {atm_strike}")
                        print(f"   - B1 Low broken: {current_sensex_ltp:.2f} < {b1['low']:.2f}")
                        print(f"   - Time since pattern: {time_since_pattern_formation:.0f}s")
                        print(f"   - Account balance available: {get_account_balance()}")
                        print(f"-----------------------------------------\n")
                        # --- END OF NEW TRADE EXECUTION VALIDATION PRINTS ---

                        # 3. Place the BUY PE order
                        order_id, entry_price = place_option_order(option_token, option_symbol, "BUY", POSITION_SIZE)

                        if order_id and entry_price > 0:
                            # Immediately verify the order was actually placed
                            immediate_status, immediate_price = get_order_status(order_id)
                            print(f"📋 Immediate order verification: Status={immediate_status}, Price={immediate_price}")
                            
                            # 4. Create and manage the new position
                            current_position = Position(
                                symbol=option_symbol,
                                token=option_token,
                                entry_price=entry_price,
                                quantity=POSITION_SIZE,
                                order_id=order_id
                            )
                            last_trade_attempt_time = datetime.now(IST) # Use IST
                            print(f"🎉 TRADE EXECUTED AT: {last_trade_attempt_time.strftime('%Y-%m-%d %H:%M:%S')}")
                            # Reset pattern_formation_time after a successful trade to look for new patterns
                            pattern_formation_time = None
                        else:
                            print("❌ Failed to execute trade. Order placement or confirmation failed.")
                            # Still set cooldown if an attempt was made, to prevent rapid retries
                            last_trade_attempt_time = datetime.now(IST)
                    else:
                        print("❌ Could not find option token. Trade aborted.")
                        last_trade_attempt_time = datetime.now(IST) # Set cooldown
                elif current_position: # If condition5 is true but position is already active
                    print("➡️ Condition 5 met, but skipping trade: A position is already active.")
                else:
                    print("❌ Condition 5 (Breakout of B1 Low by running candle) not met yet.")
            else:
                print("❌ Breakout window (15 mins) expired for this pattern. Resetting pattern.")
                pattern_formation_time = None # Reset if window expired
        else:
            print("⏳ Waiting for Condition 5 (Breakout of B1 Low) within 15 minutes.")
    else:
        # If the B1/B2 pattern (C1-C4) is not met or breaks, reset pattern_formation_time
        if pattern_formation_time is not None:
            print("❌ Pattern (C1-C4 on B1, B2) not fully met or broke. Resetting pattern formation time.")
            pattern_formation_time = None
# === WebSocket Callback Functions === # <--- You can add this heading
def on_data(ws, message):
    """Handle incoming WebSocket data with improved error handling"""
    global sensex_token, current_minute_data
    
    try:
        if isinstance(message, bytes):
            message = message.decode('utf-8')
        
        data = json.loads(message)
        
        # This function processes a list of updates or a single update
        def process_tick(tick_data):
            if isinstance(tick_data, dict) and (tick_data.get('tk') == sensex_token or tick_data.get('token') == sensex_token):
                ltp = float(tick_data.get('lp', tick_data.get('ltp', 0)))
                volume = int(tick_data.get('v', tick_data.get('volume', 0)))
                
                if ltp > 0:
                    # Keep the timestamp update for fallback/freshness validation
                    current_minute_data['last_tick_time'] = datetime.now(IST).replace(tzinfo=None)
                    print(f"📊 Live SENSEX Tick: ₹{ltp:.2f} at {current_minute_data['last_tick_time'].strftime('%H:%M:%S')}")
                    # This call now correctly updates the 5-minute candle
                    update_5min_candle(ltp, volume)

        if isinstance(data, list):
            for item in data:
                process_tick(item)
        elif isinstance(data, dict):
            process_tick(data)

    except json.JSONDecodeError as e:
        print(f"❌ JSON Decode Error: {e}")
    except Exception as e:
        print(f"❌ Error processing WebSocket data: {e}")

def update_5min_candle(ltp, volume):
    """
    CORRECTED: Aggregates live ticks into 5-minute candles.
    """
    global candle_data, current_minute_data

    # Get the timestamp for the current 5-minute interval
    current_interval = get_5min_timestamp()

    # --- CORE LOGIC FIX ---
    # Check if this is the very first tick or if a new 5-minute interval has started
    if current_minute_data.get('timestamp') is None or current_minute_data['timestamp'] != current_interval:
        # --- Finalize the PREVIOUS candle (if it exists) ---
        if current_minute_data.get('open') is not None:
            # Create a DataFrame from the completed candle data
            new_row = pd.DataFrame([current_minute_data])
            # Append it to the main candle_data DataFrame
            candle_data = pd.concat([candle_data, new_row], ignore_index=True)
            
            # Keep only the last 100 candles for performance
            if len(candle_data) > 100:
                candle_data = candle_data.tail(100).reset_index(drop=True)

            print(f"🕯️ New 5-Min Candle Closed @ {current_minute_data['timestamp'].strftime('%H:%M')}: "
                  f"O:{current_minute_data['open']:.2f} H:{current_minute_data['high']:.2f} "
                  f"L:{current_minute_data['low']:.2f} C:{current_minute_data['close']:.2f} "
                  f"V:{current_minute_data['volume']}")

        # --- Start a NEW candle for the current interval ---
        current_minute_data = {
            'timestamp': current_interval,
            'open': ltp,
            'high': ltp,
            'low': ltp,
            'close': ltp,
            'volume': volume
        }

    else:
        # --- Update the CURRENT running candle ---
        # Update high, low, close, and volume with the latest tick data
        current_minute_data['high'] = max(current_minute_data['high'], ltp)
        current_minute_data['low'] = min(current_minute_data['low'], ltp)
        current_minute_data['close'] = ltp
        # Accumulate volume over the 5-minute period
        if 'volume' in current_minute_data:
            current_minute_data['volume'] += volume
        else:
            current_minute_data['volume'] = volume

    # --- Trigger the trading logic check with every single tick ---
    # Pass the live Sensex LTP to the function to check for breakouts instantly
    if not (current_position and current_position.is_active):
        check_trading_conditions(ltp)
    
# --- WebSocket Callbacks --- (Standard callbacks for SmartWebSocketV2)
def on_close(ws, close_status_code, close_msg):
    # This callback is executed when the WS connection is closed.
    # The reconnection logic is now handled in the `start_websocket` loop itself.
    print(f"🔌 WebSocket Disconnected! Status: {close_status_code} Message: {close_msg}")

def on_error(ws, error):
    # This callback is executed when a WS error occurs.
    # The reconnection logic is now handled in the `start_websocket` loop itself.
    print(f"❌ WebSocket Error: {error}")

def on_open(ws):
    """Handle WebSocket connection open with multiple subscription attempts"""
    global reconnect_attempts, sensex_token
    
    reconnect_attempts = 0
    print("🔗 WebSocket Connected!")
    
    if sensex_token is None:
        print("❌ SENSEX token is not set. Cannot subscribe.")
        return

    # Try different exchange types for subscription
    exchange_types = [
        {"name": "BSE", "type": 1},
        {"name": "NSE", "type": 2},
        {"name": "MCX", "type": 5}
    ]
    
    subscription_success = False
    
    for exchange in exchange_types:
        try:
            subscription_data = {
                "correlationID": f"sensex_{exchange['name'].lower()}",
                "action": 1,
                "params": {
                    "mode": 1,
                    "tokenList": [{"exchangeType": exchange["type"], "tokens": [sensex_token]}]
                }
            }
            
            ws.send(json.dumps(subscription_data))
            print(f"📡 Subscribed to SENSEX on {exchange['name']} (ExchangeType: {exchange['type']})")
            subscription_success = True
            
            # Wait a bit and test if data is coming
            time.sleep(2)
            
        except Exception as e:
            print(f"❌ Error subscribing to {exchange['name']}: {e}")
            continue
    
    if not subscription_success:
        print("❌ Failed to subscribe to any exchange. Check token and connection.")

def start_websocket():
    """Start WebSocket with improved retry logic"""
    global reconnect_attempts, sensex_token
    
    # First, test if we can get LTP via REST API
    print("🧪 Testing SENSEX token via REST API...")
    test_ltp = get_ltp_data(sensex_token, "BSE")
    if test_ltp and test_ltp > 1000:
        print(f"✅ SENSEX token {sensex_token} is working. LTP: ₹{test_ltp:.2f}")
    else:
        print("❌ SENSEX token may not be working. Trying alternative tokens...")
        # Try alternative tokens
        alternative_tokens = ["99919000", "99926000", "1"]
        for alt_token in alternative_tokens:
            alt_ltp = get_ltp_data(alt_token, "BSE")
            if alt_ltp and alt_ltp > 1000:
                sensex_token = alt_token
                print(f"✅ Alternative SENSEX token {alt_token} working. LTP: ₹{alt_ltp:.2f}")
                break
    
    # Now start WebSocket
    reconnect_attempts = 0
    
    while reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            print(f"🔗 WebSocket connection attempt {reconnect_attempts + 1}/{MAX_RECONNECT_ATTEMPTS}")
            
            # Create WebSocket instance
            ws = SmartWebSocketV2(authToken, api_key, username, feed_token)
            ws.on_open = on_open
            ws.on_data = on_data
            ws.on_error = on_error
            ws.on_close = on_close
            
            # Connect (this is blocking)
            ws.connect()
            
            # If we reach here, connection was closed
            print(f"WebSocket disconnected. Reconnecting in {RECONNECT_DELAY_SECONDS} seconds...")
            reconnect_attempts += 1
            time.sleep(RECONNECT_DELAY_SECONDS)
            
        except Exception as e:
            print(f"❌ WebSocket connection error: {e}")
            reconnect_attempts += 1
            time.sleep(RECONNECT_DELAY_SECONDS)
    
    print(f"❌ Max reconnection attempts reached. WebSocket failed.")


def get_historical_data():
    """
    Load historical data to initialize `candle_data` for strategy lookback.
    This helps the bot start processing patterns without waiting for 3 full live candles.
    """
    global candle_data, smartApi, sensex_token, EXCHANGE

    if smartApi is None:
        print("❌ SmartApi object not initialized for get_historical_data.")
        return

    if sensex_token == "99919000": # If sensex_token is the fallback, historical data might not work
        print("⚠️ SENSEX token is a fallback. Historical data might not be available for this token.")
        return

    try:
        # Fetch 5-minute candles for the last few days
        from_date = (datetime.now(IST) - timedelta(days=5)).strftime('%Y-%m-%d %H:%M')
        to_date = datetime.now(IST).strftime('%Y-%m-%d %H:%M')

        print(f"📈 Loading Historical Data for {sensex_token} from {from_date} to {to_date}...")
        hist_data = smartApi.getCandleData({
            "exchange": EXCHANGE, # Use EXCHANGE variable for consistency
            "symboltoken": sensex_token,
            "interval": "FIVE_MINUTE", # Specify 5-minute candles
            "fromdate": from_date,
            "todate": to_date
        })

        if hist_data and hist_data.get('status') and hist_data.get('data'):
            hist_candles = []
            for candle in hist_data['data']:
                # Parse timestamp and convert to IST, then remove timezone info for consistency
                dt = datetime.strptime(candle[0], '%Y-%m-%dT%H:%M:%S%z').astimezone(IST).replace(tzinfo=None) # THIS IS THE NEW LINE
                open_price = float(candle[1])
                hist_candles.append({
                    'timestamp': dt,
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': int(candle[5])
                })

            # Create DataFrame, remove duplicates (if any), sort by timestamp, and keep last 100
            candle_data = pd.DataFrame(hist_candles).drop_duplicates(subset=['timestamp']).sort_values(by='timestamp').reset_index(drop=True)
            # Take only the last 100 relevant candles
            if len(candle_data) > 100:
                candle_data = candle_data.tail(100).reset_index(drop=True)
            print(f"✅ Loaded {len(candle_data)} historical 5-minute candles.")
        else:
            print(f"No historical data received or API call failed: {hist_data.get('message', 'No data')}. Starting with empty candle data.")
            candle_data = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    except Exception as e:
        print(f"❌ Error loading historical data for {sensex_token}: {e}")
        candle_data = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

def schedule_market_square_off():
    """
    Schedules a forced square-off of the current position at AUTO_SQUARE_OFF_TIME (3:25 PM IST).
    """
    global square_off_scheduled

    if square_off_scheduled:
        print("Auto square-off already scheduled for today.")
        return

    current_time = datetime.now(IST)
    
    if current_time < AUTO_SQUARE_OFF_TIME:
        time_to_wait = (AUTO_SQUARE_OFF_TIME - current_time).total_seconds()
        print(f"⏰ Scheduling auto square-off at {AUTO_SQUARE_OFF_TIME.strftime('%H:%M:%S %p')} IST (in {time_to_wait:.0f} seconds)")
        
        # Using threading.Timer to execute the square-off at the precise time
        # Lambda function used to pass current_position (which might be None) to exit_position
        square_off_timer = threading.Timer(time_to_wait, lambda: exit_position(current_position, "AUTO_SQUARE_OFF"))
        square_off_timer.daemon = True # Allows the timer thread to exit when the main program exits
        square_off_timer.start()
        square_off_scheduled = True
    else:
        print(f"🕒 Auto square-off time ({AUTO_SQUARE_OFF_TIME.strftime('%H:%M %p')} IST) has already passed for today. Checking for active positions...")
        # If time has passed, and a position is active, attempt to square off immediately.
        if current_position and current_position.is_active:
            print("Force exiting current active position due to late start past square-off time.")
            exit_position(current_position, "LATE_START_SQUARE_OFF")


# === Main Execution ===
if __name__ == "__main__":
    print("🚀 ENHANCED SENSEX Options Auto Trading System")
    print("⚡ Strategy: Bullish-Bearish-Breakdown (PE Buy) with 15-min Breakout Window")
    print("👉 Key Logic: B1 (completed green), B2 (completed red) pattern. Breakout detected by LIVE PRICE of next (running) candle below B1 low, immediately within 15 mins.")

    # Max trade amount calculation (already done outside main in your snippet, but good to ensure consistency)
    MAX_TRADE_AMOUNT = min(available_balance * MAX_TRADE_AMOUNT_PERCENT, MAX_TRADE_AMOUNT_CAP)

    print(f"💰 Max Trade Amount: ₹{MAX_TRADE_AMOUNT:.2f}")
    print(f"📦 Position Size: {POSITION_SIZE} lots")
    print(f"🎯 Target: +₹{TARGET_POINTS} | Stop Loss: -₹{STOP_LOSS_POINTS}")
    print(f"💸 Max Daily Loss: ₹{MAX_DAILY_LOSS}") # New: Display Max Daily Loss
    print(f"🕒 Entry Cutoff: {ENTRY_CUTOFF_TIME.strftime('%H:%M %p')} | Market Close: {MARKET_CLOSE_TIME.strftime('%H:%M %p')}")
    print(f"⏰ Auto Square-off: {AUTO_SQUARE_OFF_TIME.strftime('%H:%M %p')}") # New: Display Auto Square-off time
    print(f"⏳ Trade Cooldown: {MIN_TIME_BETWEEN_TRADES_SECONDS / 60:.0f} minutes")
    print("=" * 50)

    # Load historical data to seed the candle_data DataFrame
    get_historical_data()

    # Schedule the auto square-off
    schedule_market_square_off()

    # Start WebSocket in a separate thread
    websocket_thread = threading.Thread(target=start_websocket)
    websocket_thread.start()

    # Start Position Monitor in a separate thread
    position_monitor_thread = threading.Thread(target=monitor_position_loop)
    position_monitor_thread.daemon = True # Allows thread to exit when main program exits
    position_monitor_thread.start()


    # Keep the main thread alive, allowing WebSocket and monitor threads to run
    try:
        while True:
            time.sleep(1) # Sleep to prevent busy-waiting
    except KeyboardInterrupt:
        print("\n🚫 Ctrl+C detected. Shutting down bot...")
    finally:
        # Save trade log as CSV
        try:
            if trade_log:
                df = pd.DataFrame(trade_log)
                timestamp_str = datetime.now(IST).strftime('%Y%m%d_%H%M%S') # Use IST
                log_filename = f"sensex_trade_log_{timestamp_str}.csv"
                df.to_csv(log_filename, index=False)
                print(f"✅ PnL saved to '{log_filename}'")
            else:
                print("No trades executed. PnL file not created.")
        except Exception as e:
            print(f"❌ Failed to save PnL: {e}")
            
        # Logout from Angel One
        try:
            smartApi.terminateSession(username)
            print("✅ Logged out from Angel One.")
        except Exception as e:
            print(f"❌ Error during logout: {e}")
        
        print("Bot gracefully shut down.")