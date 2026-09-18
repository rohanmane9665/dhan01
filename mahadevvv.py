import pandas as pd
from datetime import datetime, timedelta, time
import time as time_module
from dhanhq import dhanhq, DhanContext, MarketFeed
import pytz
import requests
import os
import csv
from threading import Thread
import queue
import math

CLIENT_ID = "1100996819"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzg5NzE4MzI0LCJpYXQiOjE3ODk2MzE5MjQsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwOTk2ODE5In0.2CgJq2kRPyY09WmTosvvb9_zq2MJj-lR5jO_iCwRqb3q9f2TLuO8PcjaZ60j09_Etf56J_TQQx13lvIzFRLOkw"

try:
    dhan_context = DhanContext(CLIENT_ID, ACCESS_TOKEN)
    dhan = dhanhq(dhan_context)
    print("DhanHQ client initialized successfully.")
except Exception as e:
    print(f"Error initializing DhanHQ client: {e}")
    exit()
# --- 3. Define timezone ---
IST = pytz.timezone('Asia/Kolkata')

# --- 4. Define Market Trading Hours (IST) ---
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# --- 5. Known NSE/BSE Market Holidays for 2026 ---
# Source: NSE/BSE 2026 holiday circulars. Verify against
# https://www.nseindia.com/resources/exchange-communication-holidays if in doubt.
# IMPORTANT: update this again in Jan 2027 for next year's dates.
MARKET_HOLIDAYS_2026 = [
    datetime(2026, 1, 15).date(),   # Maharashtra municipal elections
    datetime(2026, 1, 26).date(),   # Republic Day
    datetime(2026, 3, 3).date(),    # Holi
    datetime(2026, 3, 26).date(),   # Shri Ram Navami
    datetime(2026, 3, 31).date(),   # Shri Mahavir Jayanti
    datetime(2026, 4, 3).date(),    # Good Friday
    datetime(2026, 4, 14).date(),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime(2026, 5, 1).date(),    # Maharashtra Day
    datetime(2026, 5, 28).date(),   # Bakri Id
    datetime(2026, 6, 26).date(),   # Muharram
    datetime(2026, 9, 14).date(),   # Ganesh Chaturthi
    datetime(2026, 10, 2).date(),   # Mahatma Gandhi Jayanti
    datetime(2026, 10, 20).date(),  # Dussehra
    datetime(2026, 11, 10).date(),  # Diwali - Balipratipada
    datetime(2026, 11, 24).date(),  # Guru Nanak Jayanti
    datetime(2026, 12, 25).date(),  # Christmas
]
# Note: Nov 8, 2026 (Sunday) has a special ~1hr Muhurat Trading session for
# Diwali. Not included here since is_trading_day() already treats Sundays as
# non-trading, and this strategy isn't built to trade a 1-hour symbolic session.

# --- 6. Global Variables for Trading ---
NIFTY50_SECURITY_ID = "13"  # BSE SENSEX Index
NIFTY50_EXCHANGE_SEGMENT = "IDX_I"
INSTRUMENT_TYPE = "INDEX" # Keep this line as is
INTERVAL = 5
OPTION_EXCHANGE_SEGMENT = dhan.BSE_FNO

# Trading state variables
position_active = False
entry_price = 0
entry_quantity = 0
remaining_quantity = 0
stop_loss = 0
target_1 = 0
target_2 = 0
target_3 = 0
day_of_week = ""
option_security_id = ""
trade_log = []
use_option_chain_pricing = False  # Set to True to use option chain method
option_current_price = 0
option_market_feed = None
option_market_feed_data = queue.Queue()
option_feed_thread = None
option_feed_active = False  # Flag to control option feed
target_1_hit = False
# Market feed variables
market_feed_data = queue.Queue()
market_feed_thread = None
current_sensex_price = 0
pattern_formation_time = None
pattern_b1_low = None
# CSV file for trade logging
trade_log_file = "trade_log.csv"

def is_trading_day(date_obj):
    """Check if a given date is a trading day"""
    if date_obj.weekday() >= 5:
        return False
    if date_obj in MARKET_HOLIDAYS_2026:
        return False
    return True

def get_next_thursday(current_date):
    """
    Get the next Thursday date, including the current day if it's Thursday.
    This is suitable for scenarios like SENSEX expiry where the current Thursday is valid.
    """
    # Thursday is 3 (Monday is 0, Tuesday is 1, Wednesday is 2, Thursday is 3, ..., Sunday is 6)
    target_weekday = 3
    current_weekday = current_date.weekday()

    days_to_add = (target_weekday - current_weekday + 7) % 7
    return current_date + timedelta(days_to_add)

def get_actual_expiry_date(current_date):
    """
    Get the ACTUAL SENSEX weekly expiry date for the week containing current_date.
    Normally this is Thursday, but BSE shifts expiry to the previous trading day
    (e.g. Wednesday, or earlier if that's also a holiday) whenever Thursday falls
    on a market holiday.
    """
    expiry = get_next_thursday(current_date)
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry

def get_current_expiry_date():
    """Get the current expiry date in the required format"""
    now = datetime.now(IST)
    expiry = get_actual_expiry_date(now.date())
    # Format as "DD MMM" (e.g., "15 JUL")
    return expiry.strftime("%d %b").upper()

def get_current_expiry_date_str():
    """Get the current expiry date in YYYY-MM-DD format"""
    now = datetime.now(IST)
    expiry = get_actual_expiry_date(now.date())
    # Format as "YYYY-MM-DD" (e.g., "2024-10-31")
    return expiry.strftime("%Y-%m-%d")

def calculate_rsi(close_series, period=14):
   # Calculate RSI using proper Wilder method
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = pd.Series(index=close_series.index, dtype=float)
    avg_loss = pd.Series(index=close_series.index, dtype=float)

    # Seed: simple average of first `period` gains/losses
    avg_gain.iloc[period] = gain.iloc[1:period+1].mean()
    avg_loss.iloc[period] = loss.iloc[1:period+1].mean()

    # Wilder smoothing for the rest
    for i in range(period + 1, len(close_series)):
        avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

# 1. MAIN ISSUE: Add position reset function after trade completion
def reset_position_variables():
    """Reset all position-related variables - ENHANCED VERSION"""
    global position_active, entry_price, entry_quantity, remaining_quantity
    global stop_loss, target_1, target_2, option_security_id, target_1_hit
    global pattern_formation_time, pattern_b1_low  # Add these pattern variables

    print("🔄 Resetting position variables...")

    # Reset position variables
    position_active = False
    entry_price = 0
    entry_quantity = 0
    remaining_quantity = 0
    stop_loss = 0
    target_1 = 0
    target_2 = 0
    target_1_hit = False

    # Reset pattern monitoring variables - CRITICAL FIX
    pattern_formation_time = None
    pattern_b1_low = None

    # Stop and reset option feed
    if option_security_id:
        print(f"🛑 Stopping option feed for security ID: {option_security_id}")
        stop_option_feed()
        option_security_id = None

    print("✅ All variables reset - Ready for new trade")

def download_master_csv():
    """Download the master CSV file from Dhan"""
    try:
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        response = requests.get(url)
        response.raise_for_status()

        with open("master_scrip.csv", "wb") as f:
            f.write(response.content)
        print("Master CSV downloaded successfully")
        return True
    except Exception as e:
        print(f"Error downloading master CSV: {e}")
        return False

def market_feed_handler():
    """Handle market feed data in a separate thread with proper event loop"""
    global current_sensex_price, market_feed_data

    import asyncio

    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Initialize market feed for SENSEX
        instruments = [(MarketFeed.IDX, SENSEX_SECURITY_ID, MarketFeed.Ticker)]
        version = "v2"

        print("Initializing SENSEX market feed...")
        market_feed = MarketFeed(dhan_context, instruments, version)
        market_feed.run_forever()
        print("SENSEX market feed initialized successfully")

        while True:
            try:
                response = market_feed.get_data()

                if response:
                    # Handle different response formats
                    if isinstance(response, dict) and 'LTP' in response:
                        current_sensex_price = float(response['LTP'])
                        market_feed_data.put(response)
                        # Removed the print statement here - ticker data will not be shown

                # time_module.sleep(1)

            except Exception as e:
                print(f"Error in market feed data processing: {e}")
                time_module.sleep(3)

    except Exception as e:
        print(f"Error initializing market feed: {e}")
    finally:
        loop.close()

def wait_for_market_feed_initialization(timeout_seconds=60):
    """Wait for market feed to initialize and get first price"""
    global current_sensex_price

    print("Waiting for market feed to initialize...")
    start_time = time_module.time()

    while current_sensex_price <= 0:
        if time_module.time() - start_time > timeout_seconds:
            print("Warning: Market feed initialization timeout")
            break
        print(f"Waiting for market data... Current price: {current_sensex_price}")
        time_module.sleep(3)  # Increased from 2 to 3 seconds

    if current_sensex_price > 0:
        print(f"Market feed initialized! Current SENSEX price: {current_sensex_price}")
        return True
    else:
        print("Market feed initialization failed")
        return False

def alternative_market_feed_handler():
    """Alternative market feed handler with proper asyncio event loop"""
    global current_sensex_price, market_feed_data

    import asyncio

    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Define instruments exactly like your working code
        instruments = [(MarketFeed.IDX, "13", MarketFeed.Ticker)]
        version = "v2"

        print("Initializing SENSEX market feed (alternative method)...")
        data = MarketFeed(dhan_context, instruments, version)
        data.run_forever()
        print("SENSEX market feed initialized successfully")

        while True:
            try:
                response = data.get_data()

                if response:
                    # Store the response silently without printing
                    # Only print if there's a significant price change (optional)

                    # Handle the response format from your working code
                    if isinstance(response, dict) and 'LTP' in response:
                        new_price = float(response['LTP'])

                        # Only update and log if price changed significantly (optional: remove this condition to store all updates)
                        if abs(new_price - current_sensex_price) > 0.1:
                            current_sensex_price = new_price
                            market_feed_data.put(response)
                            # Removed the print statement here - ticker data will not be shown
                        else:
                            # Still update the price but don't log to console
                            current_sensex_price = new_price
                            market_feed_data.put(response)

                time_module.sleep(0.1)  # Small delay to prevent overwhelming

            except Exception as e:
                print(f"Error in market feed data processing: {e}")
                time_module.sleep(1)

    except Exception as e:
        print(f"Error initializing alternative market feed: {e}")
    finally:
        loop.close()

# Add these new functions after the existing market_feed_handler function
def initialize_option_market_feed(security_id):
    """Initialize market feed for option price - FIXED VERSION"""
    global option_market_feed, option_current_price

    try:
        # Initialize the price as float
        option_current_price = 0.0

        instruments = [(MarketFeed.BSE_FNO, str(security_id), MarketFeed.Ticker)]
        version = "v2"

        option_market_feed = MarketFeed(dhan_context, instruments, version)
        option_market_feed.run_forever()
        print(f"Option market feed initialized for security ID: {security_id}")
        return True
    except Exception as e:
        print(f"Error initializing option market feed: {e}")
        return False

import asyncio
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK
import json
import ssl
import certifi

# Add these improved market feed functions

def initialize_option_market_feed_robust(security_id):
    """Robust option market feed initialization with better error handling"""
    global option_market_feed, option_current_price

    try:
        option_current_price = 0.0

        # Add retry mechanism for initialization
        max_retries = 3
        for attempt in range(max_retries):
            try:
                instruments = [(MarketFeed.BSE_FNO, str(security_id), MarketFeed.Ticker)]
                version = "v2"

                option_market_feed = MarketFeed(dhan_context, instruments, version)
                option_market_feed.run_forever()
                print(f"✅ Option market feed initialized (attempt {attempt + 1})")
                return True

            except Exception as e:
                print(f"❌ Initialization attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time_module.sleep(2)
                else:
                    return False

    except Exception as e:
        print(f"❌ Error in robust initialization: {e}")
        return False

def option_market_feed_handler_robust(security_id):
    """Robust option market feed handler for Google Colab"""
    global option_current_price, option_market_feed_data, option_feed_active

    import asyncio

    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    retry_count = 0
    max_retries = 5
    connection_timeout = 30  # seconds

    while option_feed_active and retry_count < max_retries:
        try:
            print(f"🔄 Starting option feed (attempt {retry_count + 1})")

            if not initialize_option_market_feed_robust(security_id):
                retry_count += 1
                time_module.sleep(5)
                continue

            consecutive_errors = 0
            last_price_update = time_module.time()

            while option_feed_active:
                try:
                    # Set timeout for getting data
                    response = option_market_feed.get_data()

                    if response and 'LTP' in response:
                        option_current_price = float(response['LTP'])
                        option_market_feed_data.put(response)
                        last_price_update = time_module.time()
                        consecutive_errors = 0  # Reset error counter

                        if position_active:
                            print(f"📊 Option price: {option_current_price}")

                    # Check for stale data (no updates for more than connection_timeout)
                    if time_module.time() - last_price_update > connection_timeout:
                        print("⚠️ No price updates - connection may be stale")
                        break

                    time_module.sleep(1)

                except Exception as e:
                    consecutive_errors += 1
                    if "keepalive ping timeout" in str(e) or "internal error" in str(e):
                        print(f"🔌 WebSocket connection issue: {e}")
                        break  # Break to retry connection
                    elif consecutive_errors >= 3:
                        print(f"❌ Too many consecutive errors ({consecutive_errors}): {e}")
                        break
                    else:
                        print(f"⚠️ Feed error ({consecutive_errors}/3): {e}")
                        time_module.sleep(2)

            retry_count += 1
            if option_feed_active and retry_count < max_retries:
                print(f"🔄 Retrying connection in 5 seconds...")
                time_module.sleep(5)

        except Exception as e:
            print(f"❌ Critical error in option feed: {e}")
            retry_count += 1
            time_module.sleep(5)

    if retry_count >= max_retries:
        print("❌ Max retries reached - switching to fallback method")
        # Switch to direct price fetching fallback
        option_price_fallback_handler(security_id)

    loop.close()
    print("🛑 Option market feed thread stopped")

def option_price_fallback_handler(security_id):
    """Fallback option price handler using direct API calls"""
    global option_current_price, option_feed_active

    print("🔄 Starting fallback price handler...")

    while option_feed_active:
        try:
            # Use direct price fetch as fallback
            price = get_option_price_direct(security_id)
            if price > 0:
                option_current_price = price
                if position_active:
                    print(f"📊 Option price (fallback): {price}")

            time_module.sleep(5)  # Update every 5 seconds with fallback

        except Exception as e:
            print(f"❌ Error in fallback handler: {e}")
            time_module.sleep(10)

def get_option_price_direct_improved(security_id):
    """Improved direct option price fetching with timeout and retry"""
    try:
        instruments = [(MarketFeed.BSE_FNO, str(security_id), MarketFeed.Ticker)]
        version = "v2"

        # Create connection with timeout
        data = MarketFeed(dhan_context, instruments, version)
        data.run_forever()

        # Get data with improved timeout handling
        attempts = 0
        max_attempts = 5  # Reduced attempts for faster fallback

        start_time = time_module.time()
        timeout_seconds = 10  # Reduced timeout

        while attempts < max_attempts and (time_module.time() - start_time) < timeout_seconds:
            try:
                response = data.get_data()
                if response and 'LTP' in response:
                    price = float(response['LTP'])
                    print(f"✅ Got direct price: {price}")

                    # Close connection immediately
                    try:
                        data.close()
                    except:
                        pass
                    return price

                time_module.sleep(0.5)  # Faster polling
                attempts += 1

            except Exception as e:
                if "timeout" in str(e).lower():
                    print(f"⏱️ Timeout in direct fetch (attempt {attempts + 1})")
                    break
                attempts += 1
                time_module.sleep(0.5)

        # Cleanup
        try:
            data.close()
        except:
            pass

        print(f"❌ Direct price fetch failed after {attempts} attempts")
        return 0.0

    except Exception as e:
        print(f"❌ Error in improved direct fetch: {e}")
        return 0.0

def start_option_feed_robust(security_id):
    """Start robust option market feed thread"""
    global option_feed_thread, option_current_price, option_feed_active

    if option_feed_thread is None or not option_feed_thread.is_alive():
        option_current_price = 0.0
        option_feed_active = True

        # Use the robust handler
        option_feed_thread = Thread(
            target=option_market_feed_handler_robust,
            args=(str(security_id),),
            daemon=True
        )
        option_feed_thread.start()
        print(f"🚀 Started robust option feed for security ID: {security_id}")

        # Wait longer for initialization in cloud environment
        time_module.sleep(5)

def get_option_price_hybrid_improved(security_id):
    """Improved hybrid price fetching with multiple fallbacks"""
    global option_current_price

    # Method 1: Try market feed (if available and recent)
    if isinstance(option_current_price, (int, float)) and option_current_price > 0:
        print(f"📊 Using feed price: {option_current_price}")
        return float(option_current_price)

    # Method 2: Try improved direct method
    print("🔄 Using improved direct method...")
    price = get_option_price_direct_improved(security_id)
    if price > 0:
        return price

    # Method 3: Use original direct method as final fallback
    print("🔄 Using original direct method as final fallback...")
    return get_option_price_direct(security_id)

def get_option_price_with_multiple_fallbacks(security_id, max_retries=2):
    """Enhanced option price getter with multiple fallback methods"""

    for attempt in range(max_retries):
        try:
            # Try hybrid approach first
            price = get_option_price_hybrid_improved(security_id)

            if isinstance(price, (int, float)) and price > 0:
                return float(price)

            print(f"⚠️ Price fetch attempt {attempt + 1} failed, retrying...")
            time_module.sleep(3)  # Wait between retries

        except Exception as e:
            print(f"❌ Error in price fetch attempt {attempt + 1}: {e}")
            time_module.sleep(3)

    print(f"❌ All price fetch attempts failed")
    return 0.0

# OPTIONAL: Add function to stop option feed when position is closed
def stop_option_feed():
    """Enhanced stop option feed function"""
    global option_feed_thread, option_current_price, option_market_feed, option_feed_active

    try:
        print("🛑 Stopping option market feed...")

        # Set flag to stop feed
        option_feed_active = False

        # Close market feed connection
        if option_market_feed:
            try:
                option_market_feed.close()
                print("📡 Option market feed connection closed")
            except:
                pass
            option_market_feed = None

        # Reset current price
        option_current_price = 0.0

        # Wait for thread to finish
        if option_feed_thread and option_feed_thread.is_alive():
            print("⏳ Waiting for option feed thread to stop...")
            option_feed_thread.join(timeout=10)  # Wait up to 10 seconds

        option_feed_thread = None

        # Clear any remaining data in queue
        try:
            while not option_market_feed_data.empty():
                option_market_feed_data.get_nowait()
        except:
            pass

        print("✅ Option market feed stopped successfully")

    except Exception as e:
        print(f"⚠️ Error stopping option market feed: {e}")

def complete_trade_cleanup():
    """Complete cleanup after trade completion"""
    global trade_log

    print("🧹 Starting post-trade cleanup...")

    # Reset all position variables
    reset_position_variables()

    # Add small delay to ensure cleanup is complete
    time_module.sleep(2)

    print("✅ Post-trade cleanup completed - Ready for new opportunities")

def get_sensex_live_price():
    """Get live SENSEX price from market feed"""
    global current_sensex_price

    if current_sensex_price > 0:
        return current_sensex_price

    # If market feed price is not available, return placeholder
    print(f"Warning: Market feed price not available, using placeholder")
    return 82300  # Placeholder value

def convert_to_strike_price_for_sensex(sensex_price):
    """Convert SENSEX price to nearest 100 multiple for strike price, rounding up"""
    return math.ceil(sensex_price / 100) * 100

def find_option_security_id(strike_price):
    """Find the security ID for the option from master CSV"""
    try:
        # Get dynamic expiry date instead of hardcoded one
        expiry_date = get_current_expiry_date()

        # Format: SENSEX 15 JUL 82300 PUT
        option_name = f"SENSEX {expiry_date} {strike_price} PUT"

        print(f"🔍 Looking for option: {option_name}")  # Debug print

        # Read master CSV
        try:
             with open("master_scrip.csv"):
                   pass
        except OSError:
             if not download_master_csv(): 
                return None

        # Fix the pandas warning by specifying dtype and low_memory
        df = pd.read_csv("master_scrip.csv", dtype=str, low_memory=False)

        # Print available columns for debugging
        print(f"📋 Available columns: {list(df.columns)}")

        # FIXED: Use correct column names from your CSV
        option_row = df[df['SEM_CUSTOM_SYMBOL'].str.strip() == option_name]

        if not option_row.empty:
            security_id = option_row.iloc[0]['SEM_SMST_SECURITY_ID']
            print(f"✅ Found option security ID: {security_id}")
            return security_id
        else:
            print(f"❌ Option {option_name} not found in master CSV")

            # Debug: Show similar options to help identify naming pattern
            print("🔍 Searching for similar SENSEX options...")
            sensex_options = df[df['SEM_CUSTOM_SYMBOL'].str.contains('SENSEX', na=False)]
            if not sensex_options.empty:
                print("📝 Found SENSEX options (first 10):")
                for i, row in sensex_options.head(10).iterrows():
                    print(f"   {row['SEM_CUSTOM_SYMBOL']}")

            return None

    except Exception as e:
        print(f"❌ Error finding option security ID: {e}")
        return None

def get_day_trading_params(day_name):
    """Get trading parameters based on day of week - UPDATED"""
    params = {
        'Monday': {'quantity': 60, 'lots': 3, 'stop_loss': 30, 'target_1': 27, 'target_2': None, 'target_1_qty': 2, 'sl_adjust': 7},
        'Tuesday': {'quantity': 80, 'lots': 4, 'stop_loss': 30, 'target_1': 27, 'target_2': None, 'target_1_qty': 3, 'sl_adjust': 7},
        'Wednesday': {'quantity': 40, 'lots': 2, 'stop_loss': 30, 'target_1': 27, 'target_2': None, 'target_1_qty': 1, 'sl_adjust': 7},
        'Thursday': {'quantity': 40, 'lots': 2, 'stop_loss': 30, 'target_1': 27, 'target_2': None, 'target_1_qty': 1, 'sl_adjust': 7},
        'Friday': {'quantity': 60, 'lots': 3, 'stop_loss': 30, 'target_1': 27, 'target_2': None, 'target_1_qty': 2, 'sl_adjust': 7}
    }
    return params.get(day_name, None)

def is_trading_allowed():
    """Check if new trades are allowed (before 3:10 PM)"""
    current_time = datetime.now().time()
    cutoff_time = time(15, 10)  # 3:10 PM
    return current_time < cutoff_time

def is_exit_time():
    #Check if it's time to exit all positions (3:25 PM)
    current_time = datetime.now().time()
    exit_time = time(15, 25)  # 3:25 PM
    return current_time >= exit_time

def should_exit_position():
    """Check if we should exit positions (after 3:25 PM)"""
    return is_exit_time()

def place_buy_order(security_id, quantity):
    """Place buy order for PE option with time check"""

    # Double-check time before placing order
    if not is_trading_allowed():
        print("🚫 Cannot place buy order - Trading time expired")
        return None

    try:
        # Your existing buy order code...
        order_response = dhan.place_order(
            security_id=security_id,
            exchange_segment=OPTION_EXCHANGE_SEGMENT,
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.MARGIN,
            price=0
        )

        # Rest of your existing code...

        if order_response and order_response.get('status') == 'success':
            print(f"Buy order placed successfully: {order_response}")
        else:
            print(f"Error placing buy order: {order_response}")
            return None

    except Exception as e:
        print(f"Error placing buy order: {e}")
        return None

def get_trailing_stop_levels():
    """Define trailing stop loss levels"""
    return [
        {'price_level': 44, 'stop_loss': 25},
        {'price_level': 61, 'stop_loss': 41},
        {'price_level': 77, 'stop_loss': 57},
        {'price_level': 93, 'stop_loss': 72},
        {'price_level': 108, 'stop_loss': 88},
        {'price_level': 128, 'stop_loss': 104},
        {'price_level': 150, 'stop_loss': 120},
        {'price_level': 174, 'stop_loss': 138},
        {'price_level': 195, 'stop_loss': 159},
        {'price_level': 215, 'stop_loss': 183},
        {'price_level': 245, 'stop_loss': 210},
        {'price_level': 267, 'stop_loss': 235},
        {'price_level': 290, 'stop_loss': 250},
        {'price_level': 310, 'stop_loss': 307}  # Final exit level
    ]

def get_entry_price_from_order(order_id):
    """Get entry price from order ID using trade book"""
    try:
        trade_book_response = dhan.get_trade_book(order_id)

        if trade_book_response and trade_book_response.get('status') == 'success':
            trade_data = trade_book_response.get('data', [])
            if trade_data and len(trade_data) > 0:
                # Get the traded price from the first trade entry
                entry_price = trade_data[0].get('tradedPrice', 0)
                print(f"Entry price from order ID {order_id}: {entry_price}")
                return entry_price
            else:
                print(f"No trade data found for order ID: {order_id}")
                return 0
        else:
            print(f"Error getting trade book: {trade_book_response}")
            return 0

    except Exception as e:
        print(f"Error getting entry price from order: {e}")
        return 0

def place_sell_order(security_id, quantity):
    """Enhanced sell order placement with better error handling"""
    try:
        if quantity <= 0:
            print(f"Error: Invalid quantity for sell order: {quantity}")
            return None

        print(f"Placing sell order for {quantity} quantity at market price...")

        # Add a small delay to ensure market data is fresh
        time_module.sleep(1)

        order_response = dhan.place_order(
            security_id=security_id,
            exchange_segment=OPTION_EXCHANGE_SEGMENT,
            transaction_type=dhan.SELL,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.MARGIN,
            price=0
        )

        if order_response and order_response.get('status') == 'success':
            print(f"✅ Sell order placed successfully: {order_response}")
            return order_response
        else:
            print(f"❌ Error placing sell order: {order_response}")
            return None

    except Exception as e:
        print(f"❌ Exception in place_sell_order: {e}")
        return None


def get_option_price_direct(security_id):
    """Direct option price fetching using your working market feed code - OPTIMIZED"""
    try:
        # Use your working market feed code directly
        instruments = [(MarketFeed.BSE_FNO, str(security_id), MarketFeed.Ticker)]
        version = "v2"
        data = MarketFeed(dhan_context, instruments, version)
        data.run_forever()

        # Get data with timeout - OPTIMIZED with better error handling
        attempts = 0
        max_attempts = 10  # Try for 10 seconds

        while attempts < max_attempts:
            try:
                response = data.get_data()
                if response and 'LTP' in response:
                    price = float(response['LTP'])
                    print(f"Got option price: {price}")
                    # Close the market feed connection to free resources
                    try:
                        data.close()
                    except:
                        pass
                    return price
                time_module.sleep(1)
                attempts += 1
            except Exception as e:
                print(f"Error getting data (attempt {attempts + 1}): {e}")
                time_module.sleep(1)
                attempts += 1

        # Clean up on timeout
        try:
            data.close()
        except:
            pass
        print(f"Failed to get option price after {max_attempts} attempts")
        return 0.0

    except Exception as e:
        print(f"Error in direct price fetch: {e}")
        return 0.0

def get_option_price_hybrid(security_id):
    """Hybrid approach - Try market feed first, then direct method"""
    global option_current_price

    # Method 1: Try market feed (faster if already running)
    if isinstance(option_current_price, (int, float)) and option_current_price > 0:
        print(f"Using market feed price: {option_current_price}")
        return float(option_current_price)

    # Method 2: Try direct method as fallback
    print("Market feed price not available, using direct method...")
    return get_option_price_direct(security_id)

def get_option_price_with_retry(security_id, max_retries=3):
    """Get option price with retry mechanism - ENHANCED"""
    for attempt in range(max_retries):
        try:
            # Use hybrid approach
            price = get_option_price_hybrid(security_id)

            # FIXED: Ensure numeric comparison
            if isinstance(price, (int, float)) and price > 0:
                return float(price)

            print(f"Price fetch attempt {attempt + 1} returned {price}, retrying...")
            time_module.sleep(2)  # Wait between retries

        except Exception as e:
            print(f"Error in price fetch attempt {attempt + 1}: {e}")
            time_module.sleep(2)

    print(f"Failed to get price after {max_retries} attempts")
    return 0.0

def get_option_price(security_id):
    """Main option price getter - SIMPLIFIED and RELIABLE"""
    try:
        # Always use the direct method for reliability
        return get_option_price_direct(security_id)
    except Exception as e:
        print(f"Error getting option price: {e}")
        return 0.0

def execute_stop_loss_with_retry(current_price):
    """Execute stop loss with retry mechanism - UPDATED"""
    global position_active, remaining_quantity

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Executing stop loss - Attempt {attempt + 1}")
            sell_response = place_sell_order(option_security_id, remaining_quantity)

            if sell_response and sell_response.get('status') == 'success':
                pnl = (current_price - entry_price) * remaining_quantity
                log_trade("STOP_LOSS", remaining_quantity, current_price, pnl)
                print(f"🔴 STOP LOSS EXECUTED - PnL: ₹{pnl}")

                # CRITICAL FIX: Use enhanced cleanup
                complete_trade_cleanup()

                return True
            else:
                print(f"Stop loss order failed - Attempt {attempt + 1}: {sell_response}")
                time_module.sleep(2)

        except Exception as e:
            print(f"Error executing stop loss - Attempt {attempt + 1}: {e}")
            time_module.sleep(2)

    print("CRITICAL: Stop loss execution failed after all retries")
    # Even if order fails, reset position to prevent stuck state
    complete_trade_cleanup()
    return False

def execute_target_1_with_retry(current_price, trading_params):
    """Execute target 1 with retry mechanism - UPDATED"""
    global remaining_quantity, stop_loss, target_1_hit, entry_price

    target_1_qty = trading_params['target_1_qty'] * 20

    if target_1_qty <= remaining_quantity and not target_1_hit:
        for attempt in range(3):
            try:
                print(f"Executing Target 1 - Attempt {attempt + 1}")
                sell_response = place_sell_order(option_security_id, target_1_qty)

                if sell_response and sell_response.get('status') == 'success':
                    pnl = (current_price - entry_price) * target_1_qty
                    log_trade("TARGET_1", target_1_qty, current_price, pnl)
                    remaining_quantity -= target_1_qty

                    # Mark target 1 as hit and adjust stop loss for remaining quantity
                    target_1_hit = True
                    stop_loss = entry_price + trading_params['sl_adjust']  # entry + 7

                    print(f"🎯 TARGET 1 EXECUTED - PnL: ₹{pnl}")
                    print(f"Stop loss adjusted to {stop_loss} for remaining {remaining_quantity} qty")

                    # If no remaining quantity, complete cleanup
                    if remaining_quantity <= 0:
                        print("🏁 All quantity sold at Target 1 - Completing trade")
                        complete_trade_cleanup()

                    return True
                else:
                    print(f"Target 1 order failed - Attempt {attempt + 1}")
                    time_module.sleep(2)

            except Exception as e:
                print(f"Error executing target 1 - Attempt {attempt + 1}: {e}")
                time_module.sleep(2)
    return False

def implement_advanced_trailing_stop_loss(current_price):
    """Implement advanced trailing stop loss system - UPDATED"""
    global stop_loss, entry_price, remaining_quantity, position_active

    trailing_levels = get_trailing_stop_levels()

    # Check for final exit at 310 level
    if current_price >= entry_price + 310:
        final_exit_price = entry_price + 307
        print(f"🏁 FINAL EXIT TRIGGERED at price level 310 - Selling at {final_exit_price}")
        try:
            sell_response = place_sell_order(option_security_id, remaining_quantity)
            if sell_response and sell_response.get('status') == 'success':
                pnl = (final_exit_price - entry_price) * remaining_quantity
                log_trade("FINAL_EXIT", remaining_quantity, final_exit_price, pnl)
                print(f"🏁 FINAL EXIT EXECUTED - PnL: ₹{pnl}")

                # CRITICAL FIX: Use enhanced cleanup
                complete_trade_cleanup()
                return True
        except Exception as e:
            print(f"Error executing final exit: {e}")

    # Check trailing stop loss hit
    if current_price <= stop_loss:
        print(f"📉 TRAILING STOP LOSS HIT at {current_price}")
        try:
            sell_response = place_sell_order(option_security_id, remaining_quantity)
            if sell_response and sell_response.get('status') == 'success':
                pnl = (current_price - entry_price) * remaining_quantity
                log_trade("TRAILING_SL", remaining_quantity, current_price, pnl)
                print(f"📉 TRAILING STOP LOSS EXECUTED - PnL: ₹{pnl}")

                # CRITICAL FIX: Use enhanced cleanup
                complete_trade_cleanup()
                return True
        except Exception as e:
            print(f"Error executing trailing stop loss: {e}")
            # Even if order fails, reset position
            complete_trade_cleanup()
            return False

    # Update trailing stop loss levels
    # Exclude the final exit level
    for level in trailing_levels[:-1]:  
        price_threshold = entry_price + level['price_level']
        new_stop_loss = entry_price + level['stop_loss']

        if current_price >= price_threshold and new_stop_loss > stop_loss:
            stop_loss = new_stop_loss
            print(f"📈 Trailing SL updated to {stop_loss} (Price reached: {current_price}, Threshold: {price_threshold})")
            break

    return False

def emergency_close_position():
    """Emergency close position if critical error occurs - UPDATED"""
    global position_active, remaining_quantity, entry_price, option_security_id
    if not position_active or remaining_quantity <= 0:
        print("⚠️ No active position to close")
        return

    print("🚨 EMERGENCY: Attempting to close position due to critical error")

    try:
        current_price = get_option_price_with_retry(option_security_id, max_retries=5)
        if current_price == 0:
            current_price = entry_price  # Use entry price as fallback

        sell_response = place_sell_order(option_security_id, remaining_quantity)

        if sell_response:
            pnl = (current_price - entry_price) * remaining_quantity
            log_trade("EMERGENCY_CLOSE", remaining_quantity, current_price, pnl)
            print(f"🚨 EMERGENCY CLOSE EXECUTED - PnL: ₹{pnl}")
        else:
            print("🚨 CRITICAL: Emergency close order failed")

        # CRITICAL FIX: Always reset position even if order fails
        complete_trade_cleanup()

    except Exception as e:
        print(f"🚨 CRITICAL ERROR in emergency close: {e}")
        # Force reset even on error
        complete_trade_cleanup()
def debug_system_state():
    """Debug function to check system state between trades"""
    global position_active, entry_price, remaining_quantity, option_security_id
    global pattern_formation_time, pattern_b1_low, target_1_hit

    print("🔍 === SYSTEM STATE DEBUG ===")
    print(f"Position Active: {position_active}")
    print(f"Entry Price: {entry_price}")
    print(f"Remaining Quantity: {remaining_quantity}")
    print(f"Option Security ID: {option_security_id}")
    print(f"Pattern Formation Time: {pattern_formation_time}")
    print(f"Pattern B1 Low: {pattern_b1_low}")
    print(f"Target 1 Hit: {target_1_hit}")
    print(f"Option Feed Thread Alive: {option_feed_thread.is_alive() if option_feed_thread else 'None'}")
    print(f"Option Current Price: {option_current_price}")
    print("🔍 === END DEBUG ===")

def log_trade(action, quantity, price, pnl=0):
    global day_of_week, remaining_quantity, option_security_id, trade_log, trade_log_file

    """Log trade details to CSV"""
    try:
        trade_entry = {
            'timestamp': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'),
            'action': action,
            'quantity': quantity,
            'price': price,
            'pnl': pnl,
            'day': day_of_week,
            'remaining_qty': remaining_quantity,
            'option_security_id': option_security_id
        }

        trade_log.append(trade_entry)

        # Write to CSV
        try:
            with open(trade_log_file):
                file_exists = True
        except OSError:
            file_exists = False

        with open(trade_log_file, 'a', newline='') as csvfile:
            fieldnames = ['timestamp', 'action', 'quantity', 'price', 'pnl', 'day', 'remaining_qty', 'option_security_id']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_entry)

        print(f"Trade logged: {action} {quantity} @ {price} (PnL: ₹{pnl})")

    except Exception as e:
        print(f"Error logging trade: {e}")

def get_position_status():
    """Get current position status for debugging"""
    global position_active, entry_price, option_security_id, remaining_quantity, stop_loss, target_1, target_2

    if position_active:
        current_price = get_option_price(option_security_id)
        unrealized_pnl = (current_price - entry_price) * remaining_quantity if current_price > 0 else 0

        return {
            'active': True,
            'entry_price': entry_price,
            'current_price': current_price,
            'quantity': remaining_quantity,
            'stop_loss': stop_loss,
            'target_1': target_1,
            'target_2': target_2,
            'unrealized_pnl': unrealized_pnl
        }
    return {'active': False}

def manage_position_improved():
    """Improved position management with robust price fetching"""
    global position_active, entry_price, remaining_quantity, stop_loss, target_1, target_1_hit

    if not position_active:
        print("🔍 No active position. Checking for new trading opportunities...")
        return

    try:
        # Use improved price fetching
        current_price = get_option_price_with_multiple_fallbacks(option_security_id, max_retries=3)

        if current_price == 0:
            print("❌ CRITICAL: Option price not available - attempting emergency close")
            emergency_close_position()
            return

        trading_params = get_day_trading_params(day_of_week)
        if not trading_params:
            print(f"⚠️ No trading parameters for {day_of_week}")
            return

        print(f"📊 Position status - Current: {current_price}, Entry: {entry_price}, SL: {stop_loss}, T1: {target_1}")

        # Rest of your position management logic remains the same...
        # PRIORITY 1: Check stop loss FIRST
        sl_buffer = 0.5
        if current_price <= (stop_loss + sl_buffer):
            print(f"🔴 STOP LOSS TRIGGERED at {current_price} (SL: {stop_loss})")
            execute_stop_loss_with_retry(current_price)
            return

        # PRIORITY 2: Check target 1
        if current_price >= target_1 and not target_1_hit:
            print(f"🎯 TARGET 1 TRIGGERED at {current_price} (Target: {target_1})")
            if execute_target_1_with_retry(current_price, trading_params):
                return

        # PRIORITY 3: Implement advanced trailing stop loss
        if target_1_hit and remaining_quantity > 0:
            if implement_advanced_trailing_stop_loss(current_price):
                return

    except Exception as e:
        print(f"❌ CRITICAL ERROR in position management: {e}")
        emergency_close_position()

def check_breakout_with_market_feed(b1_low):
    """Check for breakout using live market feed data within 15 minutes"""
    global pattern_formation_time, pattern_b1_low

    pattern_formation_time = datetime.now(IST)
    pattern_b1_low = b1_low

    print(f"Pattern formed at {pattern_formation_time.strftime('%H:%M:%S')}")
    print(f"Monitoring for breakout below {b1_low} for 15 minutes...")

    # Monitor for 15 minutes
    timeout = pattern_formation_time + timedelta(minutes=15)
    check_count = 0

    while datetime.now(IST) < timeout:
        try:
            check_count += 1
            current_time = datetime.now(IST)

            # Get current SENSEX price
            if current_sensex_price > 0:
                # More precise breakout detection
                if current_sensex_price < b1_low:
                    print(f"BREAKOUT DETECTED!")
                    print(f"SENSEX broke below B1 low: {b1_low}")
                    print(f"Current price: {current_sensex_price}")
                    print(f"Time: {current_time.strftime('%H:%M:%S')}")
                    return True

                # Log progress every 30 seconds - Show current price for breakout monitoring
                if check_count % 6 == 0:
                    remaining_time = (timeout - current_time).total_seconds()
                    print(f"Breakout Monitor - Current: {current_sensex_price}, Target: {b1_low}, Time left: {remaining_time:.0f}s")
            else:
                print(f"Warning: Market feed price not available, current_sensex_price: {current_sensex_price}")

            time_module.sleep(5)  # Check every 5 seconds

        except Exception as e:
            print(f"Error checking breakout: {e}")
            time_module.sleep(5)

    print("15-minute timeout reached. No breakout detected.")
    return False

def fetch_and_check_conditions_improved():
    """Fetch OHLC data and check trading conditions - UPDATED"""
    global position_active, entry_price, entry_quantity, remaining_quantity, stop_loss, target_1, day_of_week, option_security_id, target_1_hit

    now_ist = datetime.now(IST)
    today_date = now_ist.date()

    # Set day of week
    day_of_week = now_ist.strftime('%A')

    # Get trading parameters for the day
    trading_params = get_day_trading_params(day_of_week)
    if not trading_params:
        print(f"No trading parameters defined for {day_of_week}")
        return

    # Fetch OHLC data — go back extra trading days so RSI is already "warmed up" by 9:15 AM
    lookback_date = today_date
    trading_days_back = 0
    # ~15 trading days of buffer for RSI to stabilize
    while trading_days_back < 15:  
        lookback_date -= timedelta(days=1)
        if is_trading_day(lookback_date):
            trading_days_back += 1

    from_date_obj = IST.localize(datetime(lookback_date.year, lookback_date.month, lookback_date.day,
                                         MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0))
    to_date_obj = now_ist

    from_date_str = from_date_obj.strftime("%Y-%m-%d %H:%M:%S")
    to_date_str = to_date_obj.strftime("%Y-%m-%d %H:%M:%S")

    try:
        intraday_data_response = dhan.intraday_minute_data(
            security_id=SENSEX_SECURITY_ID,
            exchange_segment=SENSEX_EXCHANGE_SEGMENT,
            instrument_type=INSTRUMENT_TYPE,
            interval=INTERVAL,
            from_date=from_date_str,
            to_date=to_date_str
        )

        if (intraday_data_response and \
            intraday_data_response.get('status') == 'success' and \
            intraday_data_response.get('data') and \
            len(intraday_data_response['data'].get('open', [])) >= 3):

            ohlc_data = intraday_data_response['data']

            df = pd.DataFrame({
                'timestamp': ohlc_data['timestamp'],
                'open': ohlc_data['open'],
                'high': ohlc_data['high'],
                'low': ohlc_data['low'],
                'close': ohlc_data['close'],
                'volume': ohlc_data['volume']
            })

            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.tz_convert(IST)
            df.set_index('datetime', inplace=True)
            df.drop('timestamp', axis=1, inplace=True)

            if len(df) < 3:
                print("Not enough data for analysis")
                return

            B1 = df.iloc[-3]
            B2 = df.iloc[-2]

            # --- RSI(14) on B2 candle close ---
            closes_upto_b2 = df.iloc[:-1]['close']  # includes B1, B2; excludes currently-forming candle
            if len(closes_upto_b2) >= 15:
                rsi_series = calculate_rsi(closes_upto_b2, period=14)
                b2_rsi = rsi_series.iloc[-1]
                C12 = b2_rsi > 40
            else:
                b2_rsi = None
                C12 = False
                print(f"⚠️ Not enough candles for RSI calculation yet (have {len(closes_upto_b2)}, need 15)")

            C1 = B1['close'] > B1['open']
            C2 = B2['close'] < B2['open']
            C3 = B2['high'] > B1['high']
            C4 = B1['low'] < B2['low']
            # C5 = (B2['high'] - B2['low']) < 120 if pd.notnull(B2['high']) and pd.notnull(B2['low']) else False # Removed C5
            C6 = 8 < (B2['high'] - B1['high']) < 80 # Updated C6
            C7 = 8 < (B2['low'] - B1['low']) < 80   # Updated C7
            C8 = 5 < (B1['close'] - B1['open']) < 100 # Updated C8
            C9 = 4 < (B2['open'] - B2['close']) < 100
            C10 = (B1['high'] - B1['low']) < 150
            C11 = (B2['high'] - B2['low']) < 150  # Updated C11

            # --- ADDED PRINT STATEMENTS ---
            print(f"--- Condition Check for {now_ist.strftime('%H:%M:%S')} ---")
            print(f"B1 Close ({B1['close']}) > B1 Open ({B1['open']}): C1 = {C1}")
            print(f"B2 Close ({B2['close']}) < B2 Open ({B2['open']}): C2 = {C2}")
            print(f"B2 High ({B2['high']}) > B1 High ({B1['high']}): C3 = {C3}")
            print(f"B1 Low ({B1['low']}) < B2 Low ({B2['low']}): C4 = {C4}")
            # print(f"(B2 High - B2 Low) ({B2['high'] - B2['low'] if pd.notnull(B2['high']) and pd.notnull(B2['low']) else 'N/A'}) < 120: C5 = {C5}") # Removed C5 print
            print(f"8 < (B2 High - B1 High) ({B2['high'] - B1['high']}) < 80: C6 = {C6}") # Updated C6 print
            print(f"8 < (B2 Low - B1 Low) ({B2['low'] - B1['low']}) < 80: C7 = {C7}")     # Updated C7 print
            print(f"5 < (B1 Close - B1 Open) ({B1['close'] - B1['open']}) < 100: C8 = {C8}") # Updated C8 print
            print(f"4 < (B2 Open - B2 Close) ({B2['open'] - B2['close']}) < 100: C9 = {C9}")
            print(f"(B1 High - B1 Low) ({B1['high'] - B1['low']}) < 150: C10 = {C10}")
            print(f"(B2 High - B2 Low) ({B2['high'] - B2['low']}) < 150: C11 = {C11}")  # Updated C11 print
            print(f"B2 RSI(14) ({b2_rsi if b2_rsi is not None else 'N/A'}) > 40: C12 = {C12}")
            print(f"All conditions (C1-C4, C6-C12) met: {C1 and C2 and C3 and C4 and C6 and C7 and C8 and C9 and C10 and C11 and C12 and not position_active}")
            print(f"Position Active: {position_active}")
            # --- END OF ADDED PRINT STATEMENTS ---

            if C1 and C2 and C3 and C4 and C6 and C7 and C8 and C9 and C10 and C11 and C12 and not position_active:
                if not is_trading_allowed():
                    print("🚫 Time check failed - No new trades after 3:10 PM")
                    return

                if check_breakout_with_market_feed(B1['low']):
                    if not is_trading_allowed():
                        print("🚫 Cannot take new position - Trading time expired")
                        return

                    sensex_price = get_sensex_live_price()
                    strike_price = convert_to_strike_price_for_sensex(sensex_price)
                    option_security_id = find_option_security_id(strike_price)

                    if option_security_id:
                        # USE ROBUST OPTION FEED
                        start_option_feed_robust(option_security_id)  # This is the key change
                        order_response = place_buy_order(option_security_id, trading_params['quantity'])

                        if order_response:
                            order_id = order_response.get('data', {}).get('orderId')

                            if order_id:
                                time_module.sleep(3)  # Slightly longer wait for Colab
                                entry_price = get_entry_price_from_order(order_id)

                                if entry_price > 0:
                                    position_active = True
                                    entry_quantity = trading_params['quantity']
                                    remaining_quantity = entry_quantity
                                    target_1_hit = False

                                    stop_loss = entry_price - trading_params['stop_loss']
                                    target_1 = entry_price + trading_params['target_1']

                                    print(f"✅ Position opened: Entry={entry_price}, SL={stop_loss}, Target={target_1}")
                                    log_trade("BUY", entry_quantity, entry_price)
                                else:
                                    print("❌ Could not get entry price from order")
                            else:
                                print("❌ Could not get order ID from response")
                    else:
                        print("❌ Could not find option security ID")
    except Exception as e:
        print(f"An error occurred in debug_market_feed_status: {e}")

def wait_for_market_open(max_wait_minutes=300):
    """Wait until market opens, with optional timeout"""
    start_time = datetime.now(IST)

    while True:
        now_ist = datetime.now(IST)

        # Safety timeout check
        if (now_ist - start_time).total_seconds() > max_wait_minutes * 60:
            print("⚠️ Timeout while waiting for market to open.")
            break

        today_date = now_ist.date()
        current_time_of_day = now_ist.time()

        if not is_trading_day(today_date):
            next_day = today_date + timedelta(days=1)
            while not is_trading_day(next_day):
                next_day += timedelta(days=1)

            next_market_open = IST.localize(datetime.combine(next_day, time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0)))
            wait_seconds = (next_market_open - now_ist).total_seconds()

            print(f"Today is not a trading day. Next trading day: {next_day.strftime('%Y-%m-%d')} at 9:15 AM")
            print(f"Waiting {wait_seconds:.0f} seconds until market opens...")
            time_module.sleep(min(wait_seconds, 3600))
            continue

        market_open_time = time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0)

        if current_time_of_day >= market_open_time:
            print(f"Market is open! Current time: {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
            break
        else:
            market_open_today = IST.localize(datetime.combine(today_date, market_open_time))
            wait_seconds = (market_open_today - now_ist).total_seconds()

            print(f"Market opens in {wait_seconds:.0f} seconds at {market_open_time.strftime('%H:%M')}...")
            time_module.sleep(min(wait_seconds, 60))

def run_trading_strategy_improved():
    """Enhanced main function with Google Colab optimizations - UPDATED"""
    global market_feed_thread, position_active

    print("*** Starting Enhanced Trading Strategy with Colab Support ***")

    # Your existing initialization code...
    download_master_csv()

    # Start market feed (SENSEX feed should work fine)
    market_feed_thread = Thread(target=alternative_market_feed_handler, daemon=True)
    market_feed_thread.start()
    # Longer timeout for Colab
    if not wait_for_market_feed_initialization(timeout_seconds=90): 
        print("Failed to initialize market feed, trying original method...")
        market_feed_thread = Thread(target=market_feed_handler, daemon=True)
        market_feed_thread.start()
        wait_for_market_feed_initialization(timeout_seconds=90)

    wait_for_market_open()

    print("\n*** Market is open. Starting trading strategy... ***")

    # Reset everything at start
    complete_trade_cleanup()

    try:
        while True:
            now_in_loop = datetime.now(IST)
            current_time_of_day_in_loop = now_in_loop.time()
            market_close_time = time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, 0)

            if current_time_of_day_in_loop > market_close_time:
                print("\n*** Market has closed for the day. ***")
                if position_active:
                    print("MARKET CLOSE: Force closing position")
                    emergency_close_position()
                generate_daily_report()
                wait_for_market_open()
                continue

            # Check if we should exit all positions at 3:25 PM
            if should_exit_position() and position_active:
                print(f"⏰ Exit time reached (3:25 PM) - Closing all positions")
                emergency_close_position()
                print("🛑 No new trades after 3:25 PM - Monitoring only")
                time_module.sleep(60)
                continue

            if not is_trading_allowed():
                if not position_active:
                    print("🚫 No new trades after 3:10 PM")
                    time_module.sleep(30)
                    continue
                else:
                    print("📊 Monitoring existing position (no new trades after 3:10 PM)")

            try:
                # DEBUG: Show system state every 5 minutes when no position
                if not position_active and now_in_loop.minute % 5 == 0:
                    debug_system_state()

                # Modified section for option feed
                fetch_and_check_conditions_improved()  # Use improved version

                if position_active:
                    manage_position_improved()  # Use improved position management
                    time_module.sleep(15)  # Slightly longer interval for Colab
                else:
                    print(f"🔍 No active position - Scanning for opportunities at {now_in_loop.strftime('%H:%M:%S')}...")
                    time_module.sleep(30)  # REDUCED from 45 to 30 seconds for better opportunity detection

            except Exception as e:
                print(f"⚠️ Error in main loop: {e}")
                if position_active:
                    print("Position active during error - attempting emergency close")
                    emergency_close_position()
                else:
                    # If no position, ensure clean state
                    complete_trade_cleanup()
                print("Continuing with next iteration...")
                time_module.sleep(60)

    except KeyboardInterrupt:
        print("\n*** Shutting down trading strategy ***")
        if position_active:
            print("Emergency close due to shutdown...")
            emergency_close_position()
        complete_trade_cleanup()
        print("Shutdown complete.")
    except Exception as e:
        print(f"⚠️ Critical error in main strategy: {e}")
        if position_active:
            emergency_close_position()
        complete_trade_cleanup()

def colab_safe_market_feed():
    """Colab-safe market feed initialization"""
    try:
        # Force IPv4 for better compatibility
        import socket
        old_getaddrinfo = socket.getaddrinfo
        def new_getaddrinfo(host, port, family=0, socktype=0, proto=0, flags=0):
            return old_getaddrinfo(host, port, socket.AF_INET, socktype, proto, flags)
        socket.getaddrinfo = new_getaddrinfo

        print("✅ Network settings optimized for Colab")
        return True
    except Exception as e:
        print(f"⚠️ Could not optimize network settings: {e}")
        return False

# Call this before initializing market feeds
colab_safe_market_feed()

# Enhanced error logging for debugging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def log_colab_error(error_msg, error_type="GENERAL"):
    """Enhanced error logging for Colab debugging"""
    logger.error(f"[{error_type}] {error_msg}")
    print(f"🐛 DEBUG [{error_type}]: {error_msg}")

print("🚀 Google Colab setup completed!")
print("📝 Remember to:")
print("1. Use the robust market feed functions")
print("2. Set longer timeouts for cloud environment")
print("3. Monitor connection errors closely")
print("4. Use the improved error handling")

def generate_daily_report():
    """Generate daily PnL report"""
    if not trade_log:
        return

    total_pnl = sum(trade['pnl'] for trade in trade_log)
    total_trades = len([trade for trade in trade_log if trade['action'] == 'BUY'])

    print(f"\n*** Daily Report for {day_of_week} ***")
    print(f"Total Trades: {total_trades}")
    print(f"Total PnL: ₹{total_pnl}")
    print(f"Trade Log saved to: {trade_log_file}")

    # Clear trade log for next day
    trade_log.clear()
def debug_market_feed_status():
    """Debug function to check market feed status"""
    global current_sensex_price, market_feed_thread

    print(f"Market feed thread alive: {market_feed_thread.is_alive() if market_feed_thread else 'Not started'}")
    print(f"Current SENSEX price: {current_sensex_price}")
    print(f"Market feed data queue size: {market_feed_data.qsize()}")

if __name__ == "__main__":
    try:
        run_trading_strategy_improved()
    except Exception as e:
        print(f"Critical error in main execution: {e}")
        if position_active:
            emergency_close_position()