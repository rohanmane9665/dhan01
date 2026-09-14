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
import numpy as np
import talib

# --- 1. Your Dhan API Credentials ---
CLIENT_ID = "1100996819"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzU0NjczNDQ2LCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwMDk5NjgxOSJ9.cEwFste9eraelOYNFk7XFDJI4wEMm1qpu1kwYi9bN9n5mxb42UscaQmbrfHeq4MYl5cb8gzOHrrRa-elDumcRw"

# --- 2. Initialize the DhanHQ client ---
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
NO_FRESH_TRADE_HOUR = 14
NO_FRESH_TRADE_MINUTE = 40

# --- 5. Define known 2025 NSE/BSE Market Holidays ---
MARKET_HOLIDAYS_2025 = [
    datetime(2025, 2, 26).date(),
    datetime(2025, 3, 14).date(),
    datetime(2025, 3, 31).date(),
    datetime(2025, 4, 10).date(),
    datetime(2025, 4, 14).date(),
    datetime(2025, 4, 18).date(),
    datetime(2025, 5, 1).date(),
    datetime(2025, 8, 15).date(),
    datetime(2025, 8, 27).date(),
    datetime(2025, 10, 2).date(),
    datetime(2025, 10, 21).date(),
    datetime(2025, 10, 22).date(),
    datetime(2025, 11, 5).date(),
    datetime(2025, 12, 25).date(),
]

# --- 6. Global Variables for Trading ---
SENSEX_SECURITY_ID = "51"
SENSEX_EXCHANGE_SEGMENT = "IDX_I"
INSTRUMENT_TYPE = "INDEX"
INTERVAL = 15  # Changed to 15 minutes for RSI calculation
OPTION_EXCHANGE_SEGMENT = dhan.BSE_FNO

# Global trading variables
current_sensex_price = 0.0
market_feed_data = queue.Queue()
option_current_price = 0.0
option_market_feed_data = queue.Queue()
option_feed_thread = None
option_feed_active = False
position_active = False
current_position = None
entry_price = 0.0
stop_loss = 0.0
trail_sl = False
sensex_data_df = pd.DataFrame()
last_signal_time = None
pe_option_data_df = pd.DataFrame()
ce_option_data_df = pd.DataFrame()
current_pe_price = 0.0
current_ce_price = 0.0

# New variables for ATM strike tracking
atm_strike_price = 0
initial_sensex_price = 0
pe_option_security_id = None
ce_option_security_id = None

# Trail ATM (10-minute ATM) variables
trail_atm_pe_security_id = None
trail_atm_ce_security_id = None
trail_atm_pe_price = 0.0
trail_atm_ce_price = 0.0
trail_atm_pe_feed_active = False
trail_atm_ce_feed_active = False
trail_atm_pe_market_feed = None
trail_atm_ce_market_feed = None

# Practical ATM (breakout ATM) variables
practical_atm_security_id = None  # NEW: Store practical ATM ID
practical_atm_strike = 0           # NEW: Store practical ATM strike
practical_atm_price = 0.0
practical_atm_feed_active = False
practical_atm_market_feed = None

# NEW: Pre-fetch variables
pre_fetched_practical_ready = False  # NEW: Flag to indicate practical ATM is ready
waiting_for_breakout = False         # NEW: Flag for breakout waiting state
breakout_option_type = None 

# RSI and EMA settings
RSI_PERIOD = 14
EMA_PERIOD = 20
SIGNAL_GAP_MINUTES = 15  # 15 minutes gap between signals

def is_trading_day(date_obj):
    """Check if a given date is a trading day"""
    if date_obj.weekday() >= 5:
        return False
    if date_obj in MARKET_HOLIDAYS_2025:
        return False
    return True

def get_next_tuesday(current_date):
    """Get the next Tuesday date, including the current day if it's Tuesday."""
    target_weekday = 1
    current_weekday = current_date.weekday()
    days_to_add = (target_weekday - current_weekday + 7) % 7
    return current_date + timedelta(days_to_add)

def get_current_expiry_date():
    """Get the current expiry date in the required format"""
    now = datetime.now(IST)
    next_tuesday = get_next_tuesday(now.date())
    return next_tuesday.strftime("%d %b").upper()

def get_current_expiry_date_str():
    """Get the current expiry date in YYYY-MM-DD format"""
    now = datetime.now(IST)
    next_tuesday = get_next_tuesday(now.date())
    return next_tuesday.strftime("%Y-%m-%d")

def convert_to_strike_price_for_sensex(sensex_price):
    """Convert SENSEX price to nearest 100 multiple for strike price, rounding up"""
    return math.ceil(sensex_price / 100) * 100

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

def calculate_rsi(prices, period=14):
    """Calculate RSI using talib"""
    try:
        if len(prices) < period + 1:
            return None
        rsi = talib.RSI(np.array(prices, dtype=float), timeperiod=period)
        return rsi
    except Exception as e:
        print(f"Error calculating RSI: {e}")
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

# ADD THIS NEW FUNCTION after the above function:
def update_option_prices_and_dataframes():
    """Update both PE and CE option prices and their dataframes - SINGLE FUNCTION"""
    global current_pe_price, current_ce_price, pe_option_data_df, ce_option_data_df
    global pe_option_security_id, ce_option_security_id
    
    try:
        now = datetime.now(IST)
        
        # Get PE option price
        if pe_option_security_id:
            pe_price = get_option_price_direct(pe_option_security_id)
            if pe_price > 0:
                current_pe_price = pe_price
                update_option_dataframe(pe_price, now, 'PE')
        
        # Get CE option price  
        if ce_option_security_id:
            ce_price = get_option_price_direct(ce_option_security_id)
            if ce_price > 0:
                current_ce_price = ce_price
                update_option_dataframe(ce_price, now, 'CE')
                
    except Exception as e:
        print(f"Error updating option prices: {e}")

def update_sensex_dataframe(new_price, timestamp):
    """Update the SENSEX dataframe with new price data (15-minute candles)"""
    global sensex_data_df
    
    try:
        # Create new row
        new_row = {
            'timestamp': timestamp,
            'open': new_price,
            'high': new_price,
            'low': new_price,
            'close': new_price,
            'volume': 0
        }
        
        # Check if we need to update current candle or create new one
        if not sensex_data_df.empty:
            last_timestamp = sensex_data_df.iloc[-1]['timestamp']
            
            # If within same 15-minute interval, update current candle
            if (timestamp - last_timestamp).total_seconds() < 900:  # 15 minutes = 900 seconds
                sensex_data_df.iloc[-1, sensex_data_df.columns.get_loc('high')] = max(
                    sensex_data_df.iloc[-1]['high'], new_price
                )
                sensex_data_df.iloc[-1, sensex_data_df.columns.get_loc('low')] = min(
                    sensex_data_df.iloc[-1]['low'], new_price
                )
                sensex_data_df.iloc[-1, sensex_data_df.columns.get_loc('close')] = new_price
                sensex_data_df.iloc[-1, sensex_data_df.columns.get_loc('timestamp')] = timestamp
            else:
                # Create new candle
                sensex_data_df = pd.concat([sensex_data_df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            # First entry
            sensex_data_df = pd.DataFrame([new_row])
        
        # Keep only last 100 candles to manage memory
        if len(sensex_data_df) > 100:
            sensex_data_df = sensex_data_df.tail(100).reset_index(drop=True)
            
    except Exception as e:
        print(f"Error updating SENSEX dataframe: {e}")
def update_option_dataframe(new_price, timestamp, option_type):
    """Update the option dataframe with new price data (15-minute candles)"""
    global pe_option_data_df, ce_option_data_df
    
    try:
        # Choose the right dataframe
        if option_type == 'PE':
            option_df = pe_option_data_df
        else:
            option_df = ce_option_data_df
        
        # Create new row
        new_row = {
            'timestamp': timestamp,
            'open': new_price,
            'high': new_price,
            'low': new_price,
            'close': new_price,
            'volume': 0
        }
        
        # Check if we need to update current candle or create new one
        if not option_df.empty:
            last_timestamp = option_df.iloc[-1]['timestamp']
            
            # If within same 15-minute interval, update current candle
            if (timestamp - last_timestamp).total_seconds() < 900:  # 15 minutes = 900 seconds
                option_df.iloc[-1, option_df.columns.get_loc('high')] = max(
                    option_df.iloc[-1]['high'], new_price
                )
                option_df.iloc[-1, option_df.columns.get_loc('low')] = min(
                    option_df.iloc[-1]['low'], new_price
                )
                option_df.iloc[-1, option_df.columns.get_loc('close')] = new_price
                option_df.iloc[-1, option_df.columns.get_loc('timestamp')] = timestamp
            else:
                # Create new candle
                option_df = pd.concat([option_df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            # First entry
            option_df = pd.DataFrame([new_row])
        
        # Keep only last 100 candles to manage memory
        if len(option_df) > 100:
            option_df = option_df.tail(100).reset_index(drop=True)
        
        # Update the global dataframe
        if option_type == 'PE':
            pe_option_data_df = option_df
        else:
            ce_option_data_df = option_df
            
    except Exception as e:
        print(f"Error updating {option_type} option dataframe: {e}")

def set_atm_strike_prices():
    """Set ATM strike prices for PE and CE options based on first 10 minutes after market open"""
    global atm_strike_price, initial_sensex_price, pe_option_security_id, ce_option_security_id
    
    try:
        now = datetime.now(IST)
        market_open_time = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE)
        ten_minutes_after_open = market_open_time + timedelta(minutes=10)
        
        # Set ATM strike after 10 minutes of market open
        if now >= ten_minutes_after_open and atm_strike_price == 0:
            initial_sensex_price = current_sensex_price
            atm_strike_price = convert_to_strike_price_for_sensex(current_sensex_price)
            
            # Find PE and CE option security IDs
            pe_option_security_id = find_option_security_id(atm_strike_price, 'PUT')
            ce_option_security_id = find_option_security_id(atm_strike_price, 'CALL')
            
            print(f"🎯 ATM Strike set at {atm_strike_price} based on SENSEX price {initial_sensex_price}")
            print(f"PE Option ID: {pe_option_security_id}, CE Option ID: {ce_option_security_id}")
            
            return True
        
        return atm_strike_price > 0
        
    except Exception as e:
        print(f"Error setting ATM strike prices: {e}")
        return False

def check_pe_option_trading_conditions():
    """Check RSI conditions for PE option trading signal - FIXED VERSION"""
    global pe_option_data_df, last_signal_time
    
    try:
        # Get current ATM strike based on CURRENT SENSEX price (not fixed ATM)
        current_atm = convert_to_strike_price_for_sensex(current_sensex_price)
        current_pe_security_id = find_option_security_id(current_atm, 'PUT')
        
        if not current_pe_security_id:
            return False
              
        min_candles_required = max(10, RSI_PERIOD + 4)
        if len(pe_option_data_df) < min_candles_required:
            print(f"PE: Need {min_candles_required} candles, have {len(pe_option_data_df)}")
            return False
        
        # Calculate RSI on CURRENT ATM PE OPTION prices
        closes = pe_option_data_df['close'].values
        rsi_values = calculate_rsi(closes, RSI_PERIOD)
        
        if rsi_values is None or len(rsi_values) < 4:
            print("PE: Insufficient RSI data")
            return False
        
        # FIXED CONDITIONS - Same as CE now
        rsi_4 = rsi_values[-4]  # iloc-4
        rsi_3 = rsi_values[-3]  # iloc-3  
        rsi_2 = rsi_values[-2]  # iloc-2
        rsi_1 = rsi_values[-1]  # current
        
        # CORRECTED PE CONDITIONS (same as requirement)
        cond1 = rsi_4 < 59.99
        cond2 = rsi_3 < 59.99
        cond3 = rsi_2 > 59.99
        
        # Time and gap conditions
        now = datetime.now(IST)
        time_condition = now.time() < time(NO_FRESH_TRADE_HOUR, NO_FRESH_TRADE_MINUTE)
        
        signal_gap_condition = True
        if last_signal_time:
            time_diff = (now - last_signal_time).total_seconds() / 60
            signal_gap_condition = time_diff >= SIGNAL_GAP_MINUTES
        
        print(f"PE ATM {current_atm} RSI: [-4]:{rsi_4:.2f}, [-3]:{rsi_3:.2f}, [-2]:{rsi_2:.2f}, [-1]:{rsi_1:.2f}")
        print(f"PE Cond1: {cond1}, Cond2: {cond2}, Cond3: {cond3}, Time: {time_condition}, Gap: {signal_gap_condition}")
        
        return cond1 and cond2 and cond3 and time_condition and signal_gap_condition
        
    except Exception as e:
        print(f"Error checking PE conditions: {e}")
        return False

def check_ce_option_trading_conditions():
    """Check RSI conditions for CE option trading signal - FIXED VERSION"""
    global ce_option_data_df, last_signal_time
    
    try:
        # Get current ATM strike based on CURRENT SENSEX price (not fixed ATM)
        current_atm = convert_to_strike_price_for_sensex(current_sensex_price)
        current_ce_security_id = find_option_security_id(current_atm, 'CALL')
        
        if not current_ce_security_id:
            return False
                
        min_candles_required = max(10, RSI_PERIOD + 4)
        if len(ce_option_data_df) < min_candles_required:
            print(f"CE: Need {min_candles_required} candles, have {len(ce_option_data_df)}")
            return False
        
        # Calculate RSI on CURRENT ATM CE OPTION prices
        closes = ce_option_data_df['close'].values
        rsi_values = calculate_rsi(closes, RSI_PERIOD)
        
        if rsi_values is None or len(rsi_values) < 4:
            print("CE: Insufficient RSI data")
            return False
        
        # FIXED CONDITIONS - Same as PE now
        rsi_4 = rsi_values[-4]  # iloc-4
        rsi_3 = rsi_values[-3]  # iloc-3  
        rsi_2 = rsi_values[-2]  # iloc-2
        rsi_1 = rsi_values[-1]  # current
        
        # CORRECTED CE CONDITIONS (same as requirement)
        cond1 = rsi_4 < 59.99
        cond2 = rsi_3 < 59.99
        cond3 = rsi_2 > 59.99
        
        # Time and gap conditions
        now = datetime.now(IST)
        time_condition = now.time() < time(NO_FRESH_TRADE_HOUR, NO_FRESH_TRADE_MINUTE)
        
        signal_gap_condition = True
        if last_signal_time:
            time_diff = (now - last_signal_time).total_seconds() / 60
            signal_gap_condition = time_diff >= SIGNAL_GAP_MINUTES
        
        print(f"CE ATM {current_atm} RSI: [-4]:{rsi_4:.2f}, [-3]:{rsi_3:.2f}, [-2]:{rsi_2:.2f}, [-1]:{rsi_1:.2f}")
        print(f"CE Cond1: {cond1}, Cond2: {cond2}, Cond3: {cond3}, Time: {time_condition}, Gap: {signal_gap_condition}")
        
        return cond1 and cond2 and cond3 and time_condition and signal_gap_condition
        
    except Exception as e:
        print(f"Error checking CE conditions: {e}")
        return False

def pre_fetch_practical_atm_option(option_type):
    """Pre-fetch practical ATM option when conditions are met - BEFORE breakout"""
    global practical_atm_security_id, practical_atm_strike, practical_atm_feed_active
    global pre_fetched_practical_ready, breakout_option_type
    
    try:
        # Get current SENSEX-based ATM strike for practical trading
        practical_atm_strike = convert_to_strike_price_for_sensex(current_sensex_price)
        
        # Find the practical ATM option security ID
        if option_type == 'PE':
            practical_atm_security_id = find_option_security_id(practical_atm_strike, 'PUT')
        else:  # CE
            practical_atm_security_id = find_option_security_id(practical_atm_strike, 'CALL')
        
        if practical_atm_security_id:
            print(f"🎯 PRE-FETCHED {option_type} Practical ATM: Strike={practical_atm_strike}, ID={practical_atm_security_id}")
            
            # Start practical ATM feed immediately when conditions are met
            practical_atm_feed_active = True
            if initialize_option_market_feed(practical_atm_security_id, "practical"):
                practical_thread = Thread(target=practical_atm_feed_handler, args=[practical_atm_security_id])
                practical_thread.daemon = True
                practical_thread.start()
                print(f"📡 {option_type} Practical ATM feed started")
                
                pre_fetched_practical_ready = True
                breakout_option_type = option_type
                return True
        
        return False
        
    except Exception as e:
        print(f"Error pre-fetching practical ATM: {e}")
        return False

# ============================================================================
# SECTION 3: NEW FUNCTION - Restart Trail ATM after position close
# ============================================================================

def restart_trail_atm_feeds():
    """Restart trail ATM feeds with fresh strike prices after position is closed"""
    global trail_atm_pe_security_id, trail_atm_ce_security_id
    global trail_atm_pe_feed_active, trail_atm_ce_feed_active
    global atm_strike_price, initial_sensex_price
    
    try:
        print("🔄 RESTARTING Trail ATM feeds with fresh strike prices...")
        
        # Stop existing feeds first
        stop_trail_atm_feeds()
        time_module.sleep(2)  # Allow feeds to stop
        
        # Get NEW trail ATM strike based on current SENSEX
        initial_sensex_price = current_sensex_price
        atm_strike_price = convert_to_strike_price_for_sensex(current_sensex_price)
        
        # Find NEW trail ATM option security IDs
        trail_atm_pe_security_id = find_option_security_id(atm_strike_price, 'PUT')
        trail_atm_ce_security_id = find_option_security_id(atm_strike_price, 'CALL')
        
        print(f"🎯 NEW Trail ATM Strike: {atm_strike_price} (SENSEX: {initial_sensex_price})")
        print(f"NEW Trail PE ID: {trail_atm_pe_security_id}, NEW Trail CE ID: {trail_atm_ce_security_id}")
        
        # Start NEW trail ATM feeds
        if trail_atm_pe_security_id:
            trail_atm_pe_feed_active = True
            if initialize_option_market_feed(trail_atm_pe_security_id, "trail_pe"):
                pe_thread = Thread(target=trail_atm_pe_feed_handler)
                pe_thread.daemon = True
                pe_thread.start()
                print("📡 NEW Trail ATM PE feed started")
        
        if trail_atm_ce_security_id:
            trail_atm_ce_feed_active = True
            if initialize_option_market_feed(trail_atm_ce_security_id, "trail_ce"):
                ce_thread = Thread(target=trail_atm_ce_feed_handler)
                ce_thread.daemon = True
                ce_thread.start()
                print("📡 NEW Trail ATM CE feed started")
        
        return True
        
    except Exception as e:
        print(f"Error restarting trail ATM feeds: {e}")
        return False
    
def stop_trail_atm_feeds():
    """Stop all trail ATM market feeds - UPDATED"""
    global trail_atm_pe_feed_active, trail_atm_ce_feed_active
    global trail_atm_pe_market_feed, trail_atm_ce_market_feed
    
    print("🛑 Stopping trail ATM market feeds...")
    trail_atm_pe_feed_active = False
    trail_atm_ce_feed_active = False
    
    try:
        if trail_atm_pe_market_feed:
            trail_atm_pe_market_feed.close()
            trail_atm_pe_market_feed = None
        if trail_atm_ce_market_feed:
            trail_atm_ce_market_feed.close()
            trail_atm_ce_market_feed = None
    except Exception as e:
        print(f"Error closing trail ATM feeds: {e}")
    
    print("✅ Trail ATM feeds stopped")

def stop_practical_atm_feed():
    """Stop practical ATM market feed"""
    global practical_atm_feed_active, practical_atm_market_feed
    global pre_fetched_practical_ready, waiting_for_breakout, breakout_option_type
    
    print("🛑 Stopping practical ATM market feed...")
    practical_atm_feed_active = False
    
    try:
        if practical_atm_market_feed:
            practical_atm_market_feed.close()
            practical_atm_market_feed = None
    except Exception as e:
        print(f"Error closing practical ATM feed: {e}")
    
    # Reset practical ATM variables
    pre_fetched_practical_ready = False
    waiting_for_breakout = False
    breakout_option_type = None
    
    print("✅ Practical ATM feed stopped")

def calculate_stop_loss(option_type='PE'):
    """Calculate stop loss from CURRENT ATM option's iloc[-2] LOW"""
    try:
        # Get current ATM option dataframe
        if option_type == 'PE':
            option_df = pe_option_data_df
        else:
            option_df = ce_option_data_df
            
        if len(option_df) < 3:
            print("Not enough option data for stop loss")
            return 0.0
        
        # ALWAYS use iloc[-2] LOW as initial stop loss for BOTH PE and CE
        stop_loss_level = option_df.iloc[-2]['low']
        print(f"{option_type} Stop loss from current ATM option iloc[-2] low: {stop_loss_level}")
        
        return stop_loss_level
        
    except Exception as e:
        print(f"Error calculating stop loss: {e}")
        return 0.0


def sensex_market_feed_handler():
    """Handle SENSEX market feed"""
    global current_sensex_price
    
    try:
        market_feed = MarketFeed(dhan_context, [SENSEX_SECURITY_ID], SENSEX_EXCHANGE_SEGMENT)
        
        while True:
            try:
                response = market_feed.get_data()
                if response and 'data' in response:
                    for data in response['data']:
                        if 'LTP' in data:
                            current_sensex_price = float(data['LTP'])
                            print(f"SENSEX LTP: {current_sensex_price}")
                            market_feed_data.put(data)
            except Exception as e:
                print(f"Error in SENSEX feed: {e}")
            
            time_module.sleep(1)
            
    except Exception as e:
        print(f"Error in SENSEX market feed handler: {e}")

def trail_stop_loss():
    """Trail stop loss when option price = entry_price + iloc[-2] range"""
    global stop_loss, trail_sl, current_position
    
    try:
        if not position_active or not trail_sl or not current_position:
            return
        
        option_type = current_position.get('option_type', 'PE')
        
        # Get current ATM option dataframe and price
        if option_type == 'PE':
            option_df = pe_option_data_df
            current_option_price = current_pe_price
        else:
            option_df = ce_option_data_df
            current_option_price = current_ce_price
            
        if len(option_df) < 3 or current_option_price <= 0:
            return
        
        # Calculate trailing trigger: entry_price + (iloc[-2] high - iloc[-2] low)
        iloc_2_range = option_df.iloc[-2]['high'] - option_df.iloc[-2]['low']
        trail_trigger = entry_price + iloc_2_range
        
        if current_option_price >= trail_trigger:
            # Move SL to iloc[-3] low
            new_sl = option_df.iloc[-3]['low']
            
            # Only trail if new SL is better than current SL
            should_trail = False
            if option_type == 'PE':
                should_trail = new_sl > stop_loss  # Higher SL is better for PE
            else:  # CE
                should_trail = new_sl > stop_loss  # Higher SL is better for CE too
            
            if should_trail:
                old_sl = stop_loss
                stop_loss = new_sl
                print(f"{option_type} Stop loss trailed from {old_sl} to {stop_loss}")
                print(f"Trigger: {trail_trigger}, Current price: {current_option_price}")
        
    except Exception as e:
        print(f"Error trailing stop loss: {e}")

def find_option_security_id(strike_price, option_type='PUT'):
    """Find the security ID for the option from master CSV"""
    try:
        expiry_date = get_current_expiry_date()
        option_name = f"SENSEX {expiry_date} {strike_price} {option_type}"
        
        print(f"🔍 Looking for option: {option_name}")
        
        if not os.path.exists("master_scrip.csv"):
            if not download_master_csv():
                return None
        
        df = pd.read_csv("master_scrip.csv", dtype=str, low_memory=False)
        option_row = df[df['SEM_CUSTOM_SYMBOL'].str.strip() == option_name]
        
        if not option_row.empty:
            security_id = option_row.iloc[0]['SEM_SMST_SECURITY_ID']
            print(f"✅ Found {option_type} option security ID: {security_id}")
            return security_id
        else:
            print(f"❌ Option {option_name} not found in master CSV")
            return None
            
    except Exception as e:
        print(f"❌ Error finding option security ID: {e}")
        return None

def initialize_option_market_feed(security_id, feed_type):
    """Initializes a market feed for a specific option and assigns it globally."""
    global trail_atm_pe_market_feed, trail_atm_ce_market_feed, practical_atm_market_feed
    
    try:
        print(f"Initializing {feed_type} market feed for ID: {security_id}...")
        market_feed = MarketFeed(
            dhan_context=dhan_context,
            instruments=[(MarketFeed.BSE_FNO, str(security_id), MarketFeed.Ticker)],
            version="v2"
        )
        
        if feed_type == "trail_pe":
            trail_atm_pe_market_feed = market_feed
        elif feed_type == "trail_ce":
            trail_atm_ce_market_feed = market_feed
        elif feed_type == "practical":
            practical_atm_market_feed = market_feed
        else:
            print(f"Unknown feed type: {feed_type}")
            return False
            
        print(f"✅ {feed_type} market feed initialized.")
        return True
        
    except Exception as e:
        print(f"Error initializing {feed_type} market feed: {e}")
        return False

def practical_atm_feed_handler(security_id):
    """Handles the market feed for the 'practical' (breakout) ATM option."""
    global practical_atm_price, practical_atm_feed_active
    
    while practical_atm_feed_active:
        try:
            if practical_atm_market_feed:
                response = practical_atm_market_feed.get_data()
                if response and 'LTP' in response:
                    practical_atm_price = float(response['LTP'])
        except Exception as e:
            print(f"Error in practical ATM feed handler: {e}")
            time_module.sleep(2) # Avoid rapid error loops
    print("Practical ATM feed handler stopped.")

def trail_atm_pe_feed_handler():
    """Handles the market feed for the 'trail' PE ATM option."""
    global trail_atm_pe_price, trail_atm_pe_feed_active
    
    while trail_atm_pe_feed_active:
        try:
            if trail_atm_pe_market_feed:
                response = trail_atm_pe_market_feed.get_data()
                if response and 'LTP' in response:
                    trail_atm_pe_price = float(response['LTP'])
                    update_option_dataframe(trail_atm_pe_price, datetime.now(IST), 'PE')
        except Exception as e:
            print(f"Error in trail PE feed handler: {e}")
            time_module.sleep(2)
    print("Trail PE feed handler stopped.")

def trail_atm_ce_feed_handler():
    """Handles the market feed for the 'trail' CE ATM option."""
    global trail_atm_ce_price, trail_atm_ce_feed_active
    
    while trail_atm_ce_feed_active:
        try:
            if trail_atm_ce_market_feed:
                response = trail_atm_ce_market_feed.get_data()
                if response and 'LTP' in response:
                    trail_atm_ce_price = float(response['LTP'])
                    update_option_dataframe(trail_atm_ce_price, datetime.now(IST), 'CE')
        except Exception as e:
            print(f"Error in trail CE feed handler: {e}")
            time_module.sleep(2)
    print("Trail CE feed handler stopped.")
    
def place_option_order(security_id, quantity=1):
    """Place option buy order"""
    try:
        response = dhan.place_order(
            security_id=str(security_id),
            exchange_segment=OPTION_EXCHANGE_SEGMENT,
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.INTRADAY
        )
        
        print(f"Order placed: {response}")
        return response
        
    except Exception as e:
        print(f"Error placing order: {e}")
        return None

def close_option_position(security_id, quantity=1):
    """Close option position"""
    try:
        response = dhan.place_order(
            security_id=str(security_id),
            exchange_segment=OPTION_EXCHANGE_SEGMENT,
            transaction_type=dhan.SELL,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.INTRADAY
        )
        
        print(f"Position closed: {response}")
        return response
        
    except Exception as e:
        print(f"Error closing position: {e}")
        return None

def emergency_close_position():
    """UPDATED Emergency close position"""
    global position_active, current_position, option_feed_active
    
    try:
        if position_active and current_position:
            print("🚨 EMERGENCY CLOSE POSITION!")
            close_option_position(current_position['security_id'])
            position_active = False
            current_position = None
            
            # Stop all feeds
            stop_trail_atm_feeds()
            stop_practical_atm_feed()
            
    except Exception as e:
        print(f"Error in emergency close: {e}")

def monitor_position():
    """Monitor active position using practical ATM price (EMA logic removed)"""
    global position_active, current_position, stop_loss, trail_sl, practical_atm_feed_active
    
    while position_active:
        try:
            # Use practical ATM price for monitoring
            current_option_price = practical_atm_price
            
            if current_option_price <= 0:
                time_module.sleep(1)
                continue
            
            option_type = current_position.get('option_type', 'PE')
            sl_hit = False
            
            # Only previous-candle-low SL (index-level comparison stays as-is)
            if option_type == 'PE':
                if current_sensex_price >= stop_loss:
                    print(f"PE Stop loss hit! SENSEX: {current_sensex_price}, SL: {stop_loss}")
                    sl_hit = True
            else:
                if current_sensex_price <= stop_loss:
                    print(f"CE Stop loss hit! SENSEX: {current_sensex_price}, SL: {stop_loss}")
                    sl_hit = True
            
            if sl_hit:
                close_option_position(current_position['security_id'])
                position_active = False
                
                # Stop practical feed and restart trail ATM
                stop_practical_atm_feed()
                restart_trail_atm_feeds()
                
                print(f"{option_type} Position closed due to stop loss. Trail ATM restarted.")
                break
            
            # Trail stop loss (unchanged)
            trail_stop_loss()
            
            # Market close check (unchanged)
            now = datetime.now(IST)
            if now.time() >= time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE - 5):
                close_option_position(current_position['security_id'])
                position_active = False
                stop_practical_atm_feed()
                print("Position closed due to market close")
                break
            
            time_module.sleep(5)
            
        except Exception as e:
            print(f"Error monitoring position: {e}")
            time_module.sleep(5)

def print_condition_status():
    """Print current condition status for both PE and CE"""
    try:
        print(f"\n{'='*60}")
        print(f"CONDITION STATUS - {datetime.now(IST).strftime('%H:%M:%S')}")
        print(f"{'='*60}")
        
        # PE Conditions
        try:
            if len(pe_option_data_df) >= 4:
                pe_closes = pe_option_data_df['close'].values
                pe_rsi = calculate_rsi(pe_closes, RSI_PERIOD)
                if pe_rsi is not None and len(pe_rsi) >= 4:
                    print(f"PE RSI: [-4]:{pe_rsi[-4]:.2f} [-3]:{pe_rsi[-3]:.2f} [-2]:{pe_rsi[-2]:.2f} [-1]:{pe_rsi[-1]:.2f}")
                    pe_c1 = pe_rsi[-4] < 59.99
                    pe_c2 = pe_rsi[-3] < 59.99  
                    pe_c3 = pe_rsi[-2] > 59.99
                    print(f"PE Conditions: C1:{pe_c1} C2:{pe_c2} C3:{pe_c3}")
        except:
            print("PE: Not enough data")
            
        # CE Conditions
        try:
            if len(ce_option_data_df) >= 4:
                ce_closes = ce_option_data_df['close'].values
                ce_rsi = calculate_rsi(ce_closes, RSI_PERIOD)
                if ce_rsi is not None and len(ce_rsi) >= 4:
                    print(f"CE RSI: [-4]:{ce_rsi[-4]:.2f} [-3]:{ce_rsi[-3]:.2f} [-2]:{ce_rsi[-2]:.2f} [-1]:{ce_rsi[-1]:.2f}")
                    ce_c1 = ce_rsi[-4] < 59.99
                    ce_c2 = ce_rsi[-3] < 59.99
                    ce_c3 = ce_rsi[-2] > 59.99
                    print(f"CE Conditions: C1:{ce_c1} C2:{ce_c2} C3:{ce_c3}")
        except:
            print("CE: Not enough data")
            
        print(f"SENSEX: {current_sensex_price}, PE Price: {current_pe_price}, CE Price: {current_ce_price}")
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"Error printing status: {e}")
def get_breakout_condition(option_type='PE'):
    """Check for breakout condition on CURRENT ATM OPTION prices"""
    try:
        # Get current ATM option dataframe and price
        if option_type == 'PE':
            option_df = pe_option_data_df
            current_option_price = current_pe_price
        else:
            option_df = ce_option_data_df  
            current_option_price = current_ce_price
        
        if len(option_df) < 3 or current_option_price <= 0:
            return False
        
        # Get iloc[-2] high from CURRENT ATM OPTION data
        iloc_2_high = option_df.iloc[-2]['high']
        
        # Breakout for both PE and CE is when the current price crosses ABOVE the iloc[-2] HIGH
        breakout = current_option_price > iloc_2_high
        
        if breakout:
            print(f"🚀 {option_type} BREAKOUT CONFIRMED! Current Price: {current_option_price} > iloc[-2] High: {iloc_2_high}")
        
        return breakout
        
    except Exception as e:
        print(f"Error checking breakout: {e}")
        return False
    
def main_trading_logic():
    """UPDATED Main trading logic with pre-fetch practical ATM"""
    global position_active, current_position, entry_price, stop_loss, trail_sl
    global practical_atm_feed_active, last_signal_time
    global pre_fetched_practical_ready, waiting_for_breakout, breakout_option_type
    
    print("🚀 Starting UPDATED Trail ATM vs Pre-fetched Practical ATM trading logic...")
    
    while True:
        try:
            now = datetime.now(IST)

            # Market checks (same as before)
            if not is_trading_day(now.date()):
                print("Market is closed today")
                time_module.sleep(300)
                continue
            
            market_start_time = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
            market_end_time = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
            
            if now < market_start_time or now > market_end_time:
                print("Outside market hours")
                if now > market_end_time:
                    # Reset for next day
                    stop_trail_atm_feeds()
                    stop_practical_atm_feed()
                    atm_strike_price = 0
                time_module.sleep(60)
                continue

            # Update SENSEX dataframe
            if current_sensex_price > 0:
                update_sensex_dataframe(current_sensex_price, now)
            
            # Set trail ATM strikes and start feeds
            if not set_atm_strike_prices():
                print("Waiting for trail ATM setup...")
                time_module.sleep(30)
                continue
            
            # Skip if already in position
            if position_active:
                time_module.sleep(15)
                continue

            # Print status
            if int(now.second) % 30 == 0:
                print(f"Trail PE: {trail_atm_pe_price}, Trail CE: {trail_atm_ce_price}")
                if pre_fetched_practical_ready:
                    print(f"Practical {breakout_option_type}: {practical_atm_price} (Strike: {practical_atm_strike})")

            # ============= NEW LOGIC: Pre-fetch → Wait for Breakout → Buy =============
            
            # STEP 1: CHECK FOR PE SIGNAL AND PRE-FETCH
            if not waiting_for_breakout and check_pe_option_trading_conditions():
                print(f"🎯 PE CONDITIONS MET. Pre-fetching practical ATM...")
                last_signal_time = now
                
                if pre_fetch_practical_atm_option('PE'):
                    waiting_for_breakout = True
                    print(f"✅ PE Practical ATM pre-fetched. Now waiting for trail ATM breakout...")

            # STEP 1b: CHECK FOR CE SIGNAL AND PRE-FETCH
            elif not waiting_for_breakout and check_ce_option_trading_conditions():
                print(f"🎯 CE CONDITIONS MET. Pre-fetching practical ATM...")
                last_signal_time = now
                
                if pre_fetch_practical_atm_option('CE'):
                    waiting_for_breakout = True
                    print(f"✅ CE Practical ATM pre-fetched. Now waiting for trail ATM breakout...")

            # STEP 2: WAIT FOR BREAKOUT AND BUY PRE-FETCHED PRACTICAL ATM
            if waiting_for_breakout and pre_fetched_practical_ready:
                if get_breakout_condition(breakout_option_type):
                    print(f"🚀 {breakout_option_type} BREAKOUT CONFIRMED! Buying practical ATM...")
                    
                    # Ensure we have practical ATM price
                    if practical_atm_price > 0:
                        order_response = place_option_order(practical_atm_security_id)
                        
                        if order_response and order_response.get('status') == 'success':
                            position_active = True
                            current_position = {
                                'security_id': practical_atm_security_id,
                                'strike_price': practical_atm_strike,
                                'option_type': breakout_option_type,
                                'entry_time': now
                            }
                            entry_price = practical_atm_price
                            stop_loss = calculate_stop_loss(breakout_option_type)
                            trail_sl = True
                            
                            print(f"✅ {breakout_option_type} Position opened!")
                            print(f"Strike: {practical_atm_strike}, Entry: {entry_price}, SL: {stop_loss}")
                            
                            # *** IMPORTANT: Stop trail ATM feeds ONLY AFTER buying ***
                            stop_trail_atm_feeds()
                            
                            # Reset waiting flags
                            waiting_for_breakout = False
                            pre_fetched_practical_ready = False
                            
                            monitor_thread = Thread(target=monitor_position)
                            monitor_thread.daemon = True
                            monitor_thread.start()
                            
                        else:
                            print("❌ Order failed. Resetting...")
                            stop_practical_atm_feed()  # Stop practical feed if order fails
                
                # Check if 15-minute window expired
                elif (now - last_signal_time).total_seconds() > 900:  # 15 minutes
                    print("⏰ 15-minute breakout window expired. Resetting...")
                    stop_practical_atm_feed()  # Stop practical feed
                    waiting_for_breakout = False
            
            time_module.sleep(15)

        except KeyboardInterrupt:
            print("\nTrading stopped by user")
            stop_trail_atm_feeds()
            stop_practical_atm_feed()
            emergency_close_position()
            break
        except Exception as e:
            print(f"Error in main trading logic: {e}")
            time_module.sleep(30)

def check_breakout_within_timeframe(conditions_met_time):
    """Check if breakout happens within 15 minutes of conditions being met"""
    try:
        current_time = datetime.now(IST)
        time_diff = (current_time - conditions_met_time).total_seconds() / 60
        within_timeframe = time_diff <= 15
        
        print(f"Breakout time check: {time_diff:.1f} minutes since conditions met, Within 15min: {within_timeframe}")
        return within_timeframe
        
    except Exception as e:
        print(f"Error checking timeframe: {e}")
        return False
    
if __name__ == "__main__":
    try:
        print("🚀 Starting Option RSI Trading Strategy...")
        
        # Download master CSV if not exists
        if not os.path.exists("master_scrip.csv"):
            download_master_csv()
        # Start SENSEX market feed
        print("📡 Starting SENSEX market feed...")
        sensex_feed_thread = Thread(target=sensex_market_feed_handler)
        sensex_feed_thread.daemon = True
        sensex_feed_thread.start()
        
        # Wait for initial SENSEX price
        print("⏳ Waiting for SENSEX price...")
        while current_sensex_price <= 0:
            time_module.sleep(1)
        
        print(f"✅ Initial SENSEX price: {current_sensex_price}")
        
        # Start main trading logic
        main_trading_logic()
        
    except KeyboardInterrupt:
        print("\n🛑 Trading stopped by user")
        emergency_close_position()
    except Exception as e:
        print(f"❌ Critical error: {e}")
        emergency_close_position()

#-------------CODE FOR CALCULATE THE RSI    

#---------------------ADD CONDITIONSSS
#COND 1 = RSI ILOC-4 <59.99
#COND 2 = RSI ILOC-3 <59.99
#COND 3 = RSI ILOC-2 >59.99
#TIME == UPTO NOOO FRESH TRADE AFTER 2:40 PM

#CHECK FOR BREAKOUTTTTTTTTTs

#stop loss === iloc -2 lowwww
# after price of option goes above sl+entry price then sl move to last three one candle low 

# aapan asa pn karu shakto ki if this pattern occurs at this moment we capture the current price of sensex and get the atm strike price 
# and then we can use that atm strike price for buying purpose
#bakii procedure same asel for sl and tr 
#ajun ek we can do sl also 20 ema 
#dongha paikii je koni lavakr hit hoil te okayyy

# in the above keep 15 minute frame for rsi calculation 

# and see as after first 10 whatever the value of sensex get a atm strike based on that pe and ce also and apply and check my condition if it gets true then as sensex index value (getting from market feed )is running using
#  that value get atm strike option if pe option get satisfied then get pe option or   if ce option get satisfied then get ce option  and buy it 