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

# --- 1. Your Dhan API Credentials ---
CLIENT_ID = "1100996819"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzU4MDU1ODU0LCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwMDk5NjgxOSJ9.iYv1pUDj6699diJGfS5pW1lfwT5SUEuFYtFSVsWSju4C66XsiCUjF5TAVIVWvGcfHdJLxFFkXLshC0aXOR5Cow"

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
SENSEX_SECURITY_ID = "51"  # BSE SENSEX Index
SENSEX_EXCHANGE_SEGMENT = "IDX_I"
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
    if date_obj in MARKET_HOLIDAYS_2025:
        return False
    return True

def get_next_tuesday(current_date):
    """
    Get the next Tuesday date, including the current day if it's Tuesday.
    This is suitable for scenarios like SENSEX expiry where the current Tuesday is valid.
    """
    # Tuesday is 1 (Monday is 0, Tuesday is 1, ..., Sunday is 6)
    target_weekday = 1
    current_weekday = current_date.weekday()

    days_to_add = (target_weekday - current_weekday + 7) % 7
    return current_date + timedelta(days_to_add)

def get_current_expiry_date():
    """Get the current expiry date in the required format"""
    now = datetime.now(IST)
    next_tuesday = get_next_tuesday(now.date())
    # Format as "DD MMM" (e.g., "15 JUL")
    return next_tuesday.strftime("%d %b").upper()

def get_current_expiry_date_str():
    """Get the current expiry date in YYYY-MM-DD format"""
    now = datetime.now(IST)
    next_tuesday = get_next_tuesday(now.date())
    # Format as "YYYY-MM-DD" (e.g., "2024-10-31")
    return next_tuesday.strftime("%Y-%m-%d")

# 1. MAIN ISSUE: Add position reset function after trade completion
def reset_position_variables():
    """Reset all position-related variables"""
    global position_active, entry_price, entry_quantity, remaining_quantity
    global stop_loss, target_1, target_2, option_security_id, target_1_hit
    
    position_active = False
    entry_price = 0
    entry_quantity = 0
    remaining_quantity = 0
    stop_loss = 0
    target_1 = 0
    target_2 = 0
    option_security_id = None
    target_1_hit = False

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
                        
                time_module.sleep(1)
                        
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
        instruments = [(MarketFeed.IDX, "51", MarketFeed.Ticker)]
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
                        if abs(new_price - current_sensex_price) > 0.1:  # Only log if change > 0.1
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

def option_market_feed_handler(security_id):
    """Handle option market feed data in a separate thread with proper event loop"""
    global option_current_price, option_market_feed_data, option_feed_active
    
    import asyncio
    
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        if not initialize_option_market_feed(security_id):
            return
        
        while option_feed_active:  # Check the flag instead of running forever
            error_counter = 0  # Track number of feed errors

            try:
                response = option_market_feed.get_data()
                if response and 'LTP' in response:
                    # FIXED: Ensure price is converted to float
                    option_current_price = float(response['LTP'])
                    option_market_feed_data.put(response)
                    
                    # Only print if position is active
                    if position_active:
                        print(f"Option price updated: {option_current_price}")
                        
                time_module.sleep(1)  # Add small delay
                        
            except Exception as e:
                if option_feed_active:
                    print(f"Error in option market feed handler: {e}")
                    error_counter += 1
                    if error_counter >= 5:
                        print("❌ Too many feed errors. Triggering emergency close.")
                        emergency_close_position()
                        break
                time_module.sleep(1)
                
    except Exception as e:
        print(f"Error initializing option market feed: {e}")
    finally:
        loop.close()
        print("Option market feed thread stopped")

def start_option_feed_if_needed(security_id):
    """Start option market feed thread if not already running - FIXED VERSION"""
    global option_feed_thread, option_current_price, option_feed_active
    
    if option_feed_thread is None or not option_feed_thread.is_alive():
        option_current_price = 0.0  # Reset price as float
        option_feed_active = True   # Set flag to start feed
        option_feed_thread = Thread(target=option_market_feed_handler, args=(str(security_id),), daemon=True)
        option_feed_thread.start()
        print(f"Started option market feed thread for security ID: {security_id}")
        
        # Wait a moment for feed to initialize and get initial price
        time_module.sleep(3)


# OPTIONAL: Add function to stop option feed when position is closed
def stop_option_feed():
    """Stop option market feed when position is closed"""
    global option_feed_thread, option_current_price, option_market_feed, option_feed_active
    
    try:
        option_feed_active = False  # Set flag to stop feed
        
        if option_market_feed:
            option_market_feed.close()  # Close the market feed connection
            
        option_current_price = 0  # Reset current price
        
        # Wait for thread to finish
        if option_feed_thread and option_feed_thread.is_alive():
            option_feed_thread.join(timeout=5)  # Wait up to 5 seconds
            
        option_feed_thread = None  # Reset thread reference
        print("Option market feed stopped successfully.")
        
    except Exception as e:
        print(f"Error stopping option market feed: {e}")

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
        if not os.path.exists("master_scrip.csv"):
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
    """Check if it's time to exit all positions (3:25 PM)"""
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
            send_alert(f"📈 POSITION OPENED\n"
                f"Entry: ₹{entry_price}\n"
                f"Quantity: {entry_quantity}\n"
                f"Stop Loss: ₹{stop_loss}\n"
                f"Target 1: ₹{target_1}\n"
                f"Day: {day_of_week}")
            return order_response
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
    """Execute stop loss with retry mechanism"""
    global position_active, remaining_quantity
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Executing stop loss - Attempt {attempt + 1}")
            sell_response = place_sell_order(option_security_id, remaining_quantity)
            
            if sell_response and sell_response.get('status') == 'success':
                pnl = (current_price - entry_price) * remaining_quantity
                log_trade("STOP_LOSS", remaining_quantity, current_price, pnl)
                print(f"STOP LOSS EXECUTED - PnL: ₹{pnl}")
                
                # IMPORTANT: Reset position variables after stop loss
                reset_position_variables()
                
                # Send alert
                send_alert(f"STOP LOSS EXECUTED at {current_price} - PnL: ₹{pnl}")
                return True
            else:
                print(f"Stop loss order failed - Attempt {attempt + 1}: {sell_response}")
                time_module.sleep(2)
                
        except Exception as e:
            print(f"Error executing stop loss - Attempt {attempt + 1}: {e}")
            time_module.sleep(2)
    
    print("CRITICAL: Stop loss execution failed after all retries")
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
                    
                    print(f"TARGET 1 EXECUTED - PnL: ₹{pnl}")
                    print(f"Stop loss adjusted to {stop_loss} for remaining {remaining_quantity} qty")
                    
                    send_alert(f"TARGET 1 HIT at {current_price} - PnL: ₹{pnl}\nRemaining qty: {remaining_quantity}\nNew SL: {stop_loss}")
                    return True
                else:
                    print(f"Target 1 order failed - Attempt {attempt + 1}")
                    time_module.sleep(2)
                    
            except Exception as e:
                print(f"Error executing target 1 - Attempt {attempt + 1}: {e}")
                time_module.sleep(2)
    return False

def implement_advanced_trailing_stop_loss(current_price):
    """Implement advanced trailing stop loss system"""
    global stop_loss, entry_price, remaining_quantity, position_active
    
    trailing_levels = get_trailing_stop_levels()
    
    # Check for final exit at 310 level
    if current_price >= entry_price + 310:
        final_exit_price = entry_price + 307
        print(f"FINAL EXIT TRIGGERED at price level 310 - Selling at {final_exit_price}")
        try:
            sell_response = place_sell_order(option_security_id, remaining_quantity)
            if sell_response and sell_response.get('status') == 'success':
                pnl = (final_exit_price - entry_price) * remaining_quantity
                log_trade("FINAL_EXIT", remaining_quantity, final_exit_price, pnl)
                print(f"FINAL EXIT EXECUTED - PnL: ₹{pnl}")
                
                reset_position_variables()
                send_alert(f"FINAL EXIT at {final_exit_price} - PnL: ₹{pnl}")
                return True
        except Exception as e:
            print(f"Error executing final exit: {e}")
    
    # Check trailing stop loss hit
    if current_price <= stop_loss:
        print(f"TRAILING STOP LOSS HIT at {current_price}")
        try:
            sell_response = place_sell_order(option_security_id, remaining_quantity)
            if sell_response and sell_response.get('status') == 'success':
                pnl = (current_price - entry_price) * remaining_quantity
                log_trade("TRAILING_SL", remaining_quantity, current_price, pnl)
                print(f"TRAILING STOP LOSS EXECUTED - PnL: ₹{pnl}")
                
                reset_position_variables()
                send_alert(f"TRAILING STOP LOSS at {current_price} - PnL: ₹{pnl}")
                return True
        except Exception as e:
            print(f"Error executing trailing stop loss: {e}")
            return False
    
    # Update trailing stop loss levels
    for level in trailing_levels[:-1]:  # Exclude the final exit level
        price_threshold = entry_price + level['price_level']
        new_stop_loss = entry_price + level['stop_loss']
        
        if current_price >= price_threshold and new_stop_loss > stop_loss:
            stop_loss = new_stop_loss
            print(f"Trailing SL updated to {stop_loss} (Price reached: {current_price}, Threshold: {price_threshold})")
            break
    
    return False

def emergency_close_position():
    """Emergency close position if critical error occurs"""
    global position_active, remaining_quantity, entry_price, option_security_id
    
    if not position_active or remaining_quantity <= 0:
        return
    
    print("EMERGENCY: Attempting to close position due to critical error")
    
    try:
        current_price = get_option_price_with_retry(option_security_id, max_retries=5)
        if current_price == 0:
            current_price = entry_price  # Use entry price as fallback
        
        sell_response = place_sell_order(option_security_id, remaining_quantity)
        
        if sell_response:
            pnl = (current_price - entry_price) * remaining_quantity
            log_trade("EMERGENCY_CLOSE", remaining_quantity, current_price, pnl)
            print(f"EMERGENCY CLOSE EXECUTED - PnL: ₹{pnl}")
            
            # IMPORTANT: Reset position variables after emergency close
            reset_position_variables()
            
            send_alert(f"EMERGENCY CLOSE at {current_price} - PnL: ₹{pnl}")
        else:
            print("CRITICAL: Emergency close failed")
            
    except Exception as e:
        print(f"CRITICAL ERROR in emergency close: {e}")

# --- TELEGRAM BOT CONFIGURATION ---
bot_token = '7381636685:AAHOrr6aD9GOJd4usAcK8FQM6BwD_EZq1Sc'
receiver_chat_id = '6736587081'

def send_telegram_message(message):
    """Send message via Telegram bot"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            'chat_id': receiver_chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("✅ Telegram notification sent successfully")
        else:
            print(f"❌ Failed to send Telegram notification: {response.text}")
    except Exception as e:
        print(f"❌ Error sending Telegram notification: {e}")

def send_alert(message):
    """Send alert notification"""
    print(f"🚨 ALERT: {message}")
    send_telegram_message(f"🚨 TRADING ALERT 🚨\n\n{message}")

def test_telegram_bot():
    """Test Telegram bot functionality"""
    test_message = "🤖 Trading Bot Test Message\n\nBot is working correctly!"
    send_telegram_message(test_message)


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
        file_exists = os.path.isfile(trade_log_file)
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

def manage_position():
    """Enhanced position management with new trailing stop logic"""
    global position_active, entry_price, remaining_quantity, stop_loss, target_1, target_1_hit

    if not position_active:
        print("🔍 No active position. Checking for new trading opportunities...")
        return
    
    try:
        # Get current price with retry mechanism
        current_price = get_option_price_with_retry(option_security_id, max_retries=3)
        if current_price == 0:
            print("CRITICAL: Option price not available — assuming feed failure.")
            emergency_close_position()
            return
            
        trading_params = get_day_trading_params(day_of_week)
        if not trading_params:
            print(f"Warning: No trading parameters for {day_of_week}")
            return
        
        print(f"Managing position - Current: {current_price}, Entry: {entry_price}, SL: {stop_loss}, T1: {target_1}")
        
        # PRIORITY 1: Check stop loss FIRST
        sl_buffer = 0.5  # Small buffer to account for slippage
        if current_price <= (stop_loss + sl_buffer):
            print(f"STOP LOSS TRIGGERED at {current_price} (SL: {stop_loss})")
            execute_stop_loss_with_retry(current_price)
            return
        
        # PRIORITY 2: Check target 1 - Only if not hit yet
        if current_price >= target_1 and not target_1_hit:
            print(f"TARGET 1 TRIGGERED at {current_price} (Target: {target_1})")
            if execute_target_1_with_retry(current_price, trading_params):
                return
        
        # PRIORITY 3: Implement advanced trailing stop loss for remaining quantity
        if target_1_hit and remaining_quantity > 0:
            if implement_advanced_trailing_stop_loss(current_price):
                return
        
    except Exception as e:
        print(f"CRITICAL ERROR in position management: {e}")
        # In case of critical error, try to close position
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

def fetch_and_check_conditions():
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
    
    # Fetch OHLC data
    from_date_obj = IST.localize(datetime(today_date.year, today_date.month, today_date.day,
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
        
        if (intraday_data_response and 
            intraday_data_response.get('status') == 'success' and
            intraday_data_response.get('data') and
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
            print(f"All conditions (C1-C4, C6-C11) met: {C1 and C2 and C3 and C4 and C6 and C7 and C8 and C9 and C10 and C11 and not position_active}") # C5 removed from this check
            print(f"Position Active: {position_active}")
            # --- END OF ADDED PRINT STATEMENTS ---
            
            if C1 and C2 and C3 and C4 and C6 and C7 and C8 and C9 and C10 and C11 and not position_active: # C5 removed from this check
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
                        start_option_feed_if_needed(option_security_id)
                        order_response = place_buy_order(option_security_id, trading_params['quantity'])
                        
                        if order_response:
                            order_id = order_response.get('data', {}).get('orderId')
                            
                            if order_id:
                                time_module.sleep(2)
                                entry_price = get_entry_price_from_order(order_id)
                                
                                # --- THIS IS THE UPDATED SECTION ---
                                if entry_price > 0:
                                    position_active = True
                                    entry_quantity = trading_params['quantity']
                                    remaining_quantity = entry_quantity
                                    target_1_hit = False  # Initialize target 1 flag

                                    # Set initial stop loss and target using new parameters
                                    stop_loss = entry_price - trading_params['stop_loss']  # entry - 20
                                    target_1 = entry_price + trading_params['target_1']    # entry + 27
                                    
                                    print(f"Position opened: Entry={entry_price}, SL={stop_loss}, Target={target_1}")
                                    log_trade("BUY", entry_quantity, entry_price)

                                    # Send a detailed alert for the new position
                                    send_alert(f"📈 POSITION OPENED\n\n"
                                             f"Entry: ₹{entry_price}\n"
                                             f"Quantity: {entry_quantity}\n"
                                             f"Stop Loss: ₹{stop_loss}\n"
                                             f"Target 1: ₹{target_1}\n"
                                             f"Day: {day_of_week}")
                                else:
                                    print("Could not get entry price from order")
                            else:
                                print("Could not get order ID from response")
                    else:
                        print("Could not find option security ID")
            
        if position_active:
            manage_position()
            
    except Exception as e:
        print(f"Error fetching data: {e}")

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
            
def run_trading_strategy():
    """Enhanced main function with time restrictions"""
    global market_feed_thread, position_active
    
    print("*** Starting Enhanced Trading Strategy ***")
    
    # Your existing initialization code...
    download_master_csv()
    
    # Start market feed
    market_feed_thread = Thread(target=alternative_market_feed_handler, daemon=True)
    market_feed_thread.start()
    
    if not wait_for_market_feed_initialization(timeout_seconds=60):
        print("Failed to initialize market feed, trying original method...")
        market_feed_thread = Thread(target=market_feed_handler, daemon=True)
        market_feed_thread.start()
        wait_for_market_feed_initialization(timeout_seconds=60)

    wait_for_market_open()
    
    print("\n*** Market is open. Starting trading strategy... ***")
    
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
            
            # Only take new trades before 3:10 PM
            if not is_trading_allowed():
                if not position_active:
                    print("🚫 No new trades after 3:10 PM")
                    time_module.sleep(30)
                    continue
                else:
                    print("📊 Monitoring existing position (no new trades after 3:10 PM)")
            
            try:
                # IMPORTANT: Always check conditions (for new trades when no position active)
                fetch_and_check_conditions()
                
                # If position is active, manage it
                if position_active:
                    manage_position()
                    time_module.sleep(10)  # Check position every 10 seconds
                else:
                    print("🔍 No active position - Scanning for new opportunities...")
                    time_module.sleep(30)  # Check for new opportunities every 30 seconds
                    
            except Exception as e:
                print(f"Error in main loop: {e}")
                if position_active:
                    print("Position active during error - attempting emergency close")
                    emergency_close_position()
                print("Continuing with next iteration...")
                time_module.sleep(60)
                
    except KeyboardInterrupt:
        print("\n*** Shutting down trading strategy ***")
        if position_active:
            print("Emergency close due to shutdown...")
            emergency_close_position()
        stop_option_feed()  # Ensure option feed is stopped
        print("Shutdown complete.")
    except Exception as e:
        print(f"Critical error in main strategy: {e}")
        if position_active:
            emergency_close_position()
        stop_option_feed()  # Ensure option feed is stopped

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
    run_trading_strategy()