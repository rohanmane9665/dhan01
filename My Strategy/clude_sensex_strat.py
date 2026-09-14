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
import asyncio
# --- 1. Your Dhan API Credentials ---
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

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

def option_market_feed_handler(security_id):
    """Handle Option market feed data in a separate thread."""
    global option_current_price, option_market_feed_data

    # Callback function for the option feed
    def on_tick(response):
        try:
            if response and response.get('feed_type') == 'ticker' and response.get('LTP'):
                option_current_price = response['LTP']
                option_market_feed_data.put(response)
        except Exception as e:
            print(f"Error in Option on_tick callback: {e}")

    try:
        # 1. Create and set the event loop for the option feed thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 2. Define the option instrument to subscribe to
        instruments = [(dhan.BSE_FNO, security_id)]

        # 3. Initialize the feed with the callback
        option_market_feed = MarketFeed(
            client_id=CLIENT_ID,
            access_token=ACCESS_TOKEN,
            instruments=instruments,
            on_tick=on_tick
        )

        # 4. Run the feed
        print(f"Option market feed started for security ID: {security_id}")
        option_market_feed.run_forever()

    except Exception as e:
        print(f"CRITICAL Error initializing Option market feed for {security_id}: {e}")

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
    
# Alternative market feed handler that matches your working code exactly
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

def start_option_feed_if_needed(security_id):
    """Start option market feed thread if not already running."""
    global option_feed_thread
    
    if option_feed_thread is None or not option_feed_thread.is_alive():
        option_feed_thread = Thread(target=option_market_feed_handler, args=(security_id,), daemon=True)
        option_feed_thread.start()
        
        # Wait a moment for feed to initialize and get the first price
        time_module.sleep(3)

# OPTIONAL: Add function to stop option feed when position is closed
def stop_option_feed():
    """Stop option market feed when position is closed"""
    global option_feed_thread, option_current_price, option_market_feed
    
    try:
        if option_market_feed:
            option_market_feed.close() # Close the market feed connection
        option_current_price = 0 # Reset current price
        print("Option market feed stopped.")
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
    """Convert SENSEX price to nearest 100 multiple for strike price"""
    return round(sensex_price / 100) * 100

def find_option_security_id(strike_price):
    """Find the security ID for the option from master CSV"""
    try:
        # Get dynamic expiry date instead of hardcoded one
        expiry_date = get_current_expiry_date()
        
        # Format: SENSEX 15 JUL 82300 PUT
        option_name = f"SENSEX {expiry_date} {strike_price} PUT"
        
        # Read master CSV
        if not os.path.exists("master_scrip.csv"):
            if not download_master_csv():
                return None
        
        df = pd.read_csv("master_scrip.csv")
        
        # Search for the option
        option_row = df[df['DISPLAY_NAME'] == option_name]
        
        if not option_row.empty:
            return option_row.iloc[0]['SECURITY_ID']
        else:
            print(f"Option {option_name} not found in master CSV")
            return None
            
    except Exception as e:
        print(f"Error finding option security ID: {e}")
        return None

def get_day_trading_params(day_name):
    """Get trading parameters based on day of week"""
    params = {
        'Monday': {'quantity': 80, 'lots': 4, 'stop_loss': 25, 'target_1': 30, 'target_2': 45, 'target_1_qty': 3, 'sl_adjust': 10},
        'Tuesday': {'quantity': 100, 'lots': 5, 'stop_loss': 25, 'target_1': 25, 'target_2': 40, 'target_1_qty': 3, 'sl_adjust': 10},
        'Wednesday': {'quantity': 40, 'lots': 2, 'stop_loss': 30, 'target_1': 30, 'target_2': 45, 'target_1_qty': 1, 'sl_adjust': 15},
        'Thursday': {'quantity': 60, 'lots': 3, 'stop_loss': 30, 'target_1': 30, 'target_2': 45, 'target_1_qty': 2, 'sl_adjust': 15},
        'Friday': {'quantity': 80, 'lots': 4, 'stop_loss': 25, 'target_1': 25, 'target_2': 40, 'target_1_qty': 3, 'sl_adjust': 10}
    }
    return params.get(day_name, None)

def place_buy_order(security_id, quantity):
    """Place buy order for PE option and return order details"""
    try:
        order_response = dhan.place_order(
            security_id=security_id,
            exchange_segment=OPTION_EXCHANGE_SEGMENT, # For SENSEX options
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            price=0
        )
        
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
            product_type=dhan.INTRA,
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
    
def get_option_price(security_id):
    """Get current option price using MarketFeed as primary method"""
    global option_current_price
    
    # Primary method: Use MarketFeed for live price
    try:
        # Start option market feed thread if not already running
        start_option_feed_if_needed(security_id)
        
        # Return current price from market feed
        if option_current_price > 0:
            return option_current_price
        else:
            print("Market feed price not available, trying fallback method...")
    except Exception as e:
        print(f"Error getting option price via market feed: {e}")
    
    # Fallback: Use option chain method
    try:
        # Get current parameters needed for option chain
        sensex_price = get_sensex_live_price()
        strike_price = convert_to_strike_price_for_sensex(sensex_price)
        expiry_date_str = get_current_expiry_date_str()
        
        # Call your existing option chain function
        option_price = get_option_price_from_underlying_details(
            security_id=SENSEX_SECURITY_ID,
            exchange_segment=SENSEX_EXCHANGE_SEGMENT,
            expiry_date_str=expiry_date_str,
            strike_price=strike_price,
            option_type_str="PE"
        )
        
        if option_price > 0:
            return option_price
            
    except Exception as e:
        print(f"Error getting option price via option chain: {e}")
    
    return 0
    
def get_option_price_from_underlying_details(security_id, exchange_segment, expiry_date_str, strike_price, option_type_str):

    """
    Get current option price by querying the option chain using underlying details.
    
    Args:
        security_id (str): Security ID of the underlying instrument (e.g., "51" for Nifty).
        IDX_I: Exchange segment of underlying (e.g., dhan.IDX_I).
        expiry_date_str (str): Expiry Date of Option in "YYYY-MM-DD" format.
        strike_price (float): The strike price of the option (e.g., 20000.0).
        option_type_str (str): The type of option, either "CE" (Call) or "PE" (Put).
    
    Returns:
        float: The Last Traded Price (LTP) of the option, or 0 if not found/error.
    """
    try:
        option_chain_response = dhan.option_chain(
            under_security_id=security_id,
            under_exchange_segment=exchange_segment,
            expiry=expiry_date_str
        )

        if option_chain_response and option_chain_response.get('status') == 'success':
            option_chain_data = option_chain_response.get('data', {}).get('oc', {})
            
            # Format strike_price to match the keys in the API response (e.g., "20000.000000")
            # This is crucial for matching the dictionary keys from the API response
            strike_key = f"{float(strike_price):.6f}" 
            
            if strike_key in option_chain_data:
                option_data_for_strike = option_chain_data[strike_key]
                
                if option_type_str.upper() == "CE" and "ce" in option_data_for_strike:
                    return option_data_for_strike["ce"].get("last_price", 0)
                elif option_type_str.upper() == "PE" and "pe" in option_data_for_strike:
                    return option_data_for_strike["pe"].get("last_price", 0)
                else:
                    print(f"Option type {option_type_str} not found for strike {strike_price} in option chain data.")
                    return 0
            else:
                print(f"Strike price {strike_price} not found in option chain for expiry {expiry_date_str}.")
                return 0
        else:
            print(f"Error fetching option chain: {option_chain_response}")
            return 0
    except Exception as e:
        print(f"Error getting option price from option chain: {e}")
        return 0

# ========================================

def get_option_price_with_retry(security_id, max_retries=3):
    """Get option price with retry mechanism"""
    for attempt in range(max_retries):
        try:
            price = get_option_price(security_id)
            if price > 0:
                return price
            print(f"Price fetch attempt {attempt + 1} returned 0, retrying...")
            time_module.sleep(1)
        except Exception as e:
            print(f"Error in price fetch attempt {attempt + 1}: {e}")
            time_module.sleep(1)
    
    print(f"Failed to get price after {max_retries} attempts")
    return 0

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
                position_active = False
                remaining_quantity = 0
                
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
    """Execute target 1 with retry mechanism"""
    global remaining_quantity, stop_loss, target_2
    
    target_1_qty = trading_params['target_1_qty'] * 20
    
    if target_1_qty <= remaining_quantity:
        for attempt in range(3):
            try:
                print(f"Executing Target 1 - Attempt {attempt + 1}")
                sell_response = place_sell_order(option_security_id, target_1_qty)
                
                if sell_response and sell_response.get('status') == 'success':
                    pnl = (current_price - entry_price) * target_1_qty
                    log_trade("TARGET_1", target_1_qty, current_price, pnl)
                    remaining_quantity -= target_1_qty
                    
                    # Adjust stop loss
                    old_stop_loss = stop_loss
                    stop_loss = entry_price + trading_params['sl_adjust']
                    target_2 = entry_price + trading_params['target_2']
                    
                    print(f"TARGET 1 EXECUTED - PnL: ₹{pnl}")
                    print(f"Stop loss adjusted from {old_stop_loss} to {stop_loss}")
                    
                    send_alert(f"TARGET 1 HIT at {current_price} - PnL: ₹{pnl}")
                    return True
                else:
                    print(f"Target 1 order failed - Attempt {attempt + 1}")
                    time_module.sleep(2)
                    
            except Exception as e:
                print(f"Error executing target 1 - Attempt {attempt + 1}: {e}")
                time_module.sleep(2)
    
    return False

def execute_target_2_with_retry(current_price, trading_params):
    global remaining_quantity, position_active, entry_price, option_security_id, day_of_week
    """Execute target 2 with retry mechanism"""  
    for attempt in range(3):
        try:
            print(f"Executing Target 2 - Attempt {attempt + 1}")
            
            if day_of_week == 'Tuesday':
                # For Tuesday, sell 1 lot and implement trailing stop loss
                sell_qty = min(20, remaining_quantity)
                
                if sell_qty > 0:
                    sell_response = place_sell_order(option_security_id, sell_qty)
                    if sell_response and sell_response.get('status') == 'success':
                        pnl = (current_price - entry_price) * sell_qty
                        log_trade("TARGET_2", sell_qty, current_price, pnl)
                        remaining_quantity -= sell_qty
                        print(f"Target 2 partial executed - PnL: ₹{pnl}, Remaining: {remaining_quantity}")
                        
                        send_alert(f"TARGET 2 PARTIAL HIT at {current_price} - PnL: ₹{pnl}")
                        
                        if remaining_quantity <= 0:
                            position_active = False
                        return True
            else:
                # For other days, sell remaining quantity
                sell_response = place_sell_order(option_security_id, remaining_quantity)
                if sell_response and sell_response.get('status') == 'success':
                    pnl = (current_price - entry_price) * remaining_quantity
                    log_trade("TARGET_2", remaining_quantity, current_price, pnl)
                    print(f"Target 2 full executed - PnL: ₹{pnl}")
                    
                    send_alert(f"TARGET 2 FULL HIT at {current_price} - PnL: ₹{pnl}")
                    
                    remaining_quantity = 0
                    position_active = False
                    return True
            
        except Exception as e:
            print(f"Error executing target 2 - Attempt {attempt + 1}: {e}")
            time_module.sleep(2)
    
    return False

def emergency_close_position():
    global position_active, remaining_quantity, entry_price, option_security_id
    """Emergency close position if critical error occurs"""
    
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
            position_active = False
            remaining_quantity = 0
            
            send_alert(f"EMERGENCY CLOSE at {current_price} - PnL: ₹{pnl}")
        else:
            print("CRITICAL: Emergency close failed")
            
    except Exception as e:
        print(f"CRITICAL ERROR in emergency close: {e}")

# --- TELEGRAM BOT CONFIGURATION ---
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
receiver_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

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
    """Enhanced position management with better error handling"""
    global position_active, entry_price, remaining_quantity, stop_loss, target_1, target_2, entry_quantity

    if not position_active:
        return
    
    try:
        # Get current price with retry mechanism
        current_price = get_option_price_with_retry(option_security_id, max_retries=3)
        if current_price == 0:
            print("CRITICAL: Could not get current option price after retries")
            return
            
        trading_params = get_day_trading_params(day_of_week)
        if not trading_params:
            print(f"Warning: No trading parameters for {day_of_week}")
            return
        
        print(f"Managing position - Current: {current_price}, Entry: {entry_price}, SL: {stop_loss}, T1: {target_1}")
        
        # PRIORITY 1: Check stop loss FIRST with small buffer
        sl_buffer = 0.5  # Small buffer to account for slippage
        if current_price <= (stop_loss + sl_buffer):
            print(f"STOP LOSS TRIGGERED at {current_price} (SL: {stop_loss})")
            execute_stop_loss_with_retry(current_price)
            return
        
        # PRIORITY 2: Check target 1 - Only execute if we haven't sold target 1 quantity yet
        if current_price >= target_1 and remaining_quantity == entry_quantity:
            print(f"TARGET 1 TRIGGERED at {current_price} (Target: {target_1})")
            execute_target_1_with_retry(current_price, trading_params)
            return
        
        # PRIORITY 3: Check target 2 - Only execute if we have remaining quantity after target 1
        if current_price >= target_2 and remaining_quantity > 0 and remaining_quantity < entry_quantity:
            print(f"TARGET 2 TRIGGERED at {current_price} (Target: {target_2})")
            execute_target_2_with_retry(current_price, trading_params)
            return
        
        # Continue trailing stop loss for Tuesday
        if day_of_week == 'Tuesday' and remaining_quantity > 0 and remaining_quantity < entry_quantity:
            implement_trailing_stop_loss(current_price)
            # Add this after successful position opening (around line 743)
        
    except Exception as e:
        print(f"CRITICAL ERROR in position management: {e}")
        # In case of critical error, try to close position
        emergency_close_position()
    
    # For Tuesday, continue trailing stop loss management after target 2
    if day_of_week == 'Tuesday' and remaining_quantity > 0 and remaining_quantity < entry_quantity:
        implement_trailing_stop_loss(current_price)

def implement_trailing_stop_loss(current_price):
    """Implement trailing stop loss for Tuesday's remaining quantity"""
    global stop_loss, entry_price

    
    # FIXED: Proper trailing stop loss logic
    # When price goes above entry + 40, set stop loss to entry + 20
    if current_price >= entry_price + 40:
        new_stop_loss = entry_price + 20
        if new_stop_loss > stop_loss:  # Only move stop loss up, never down
            stop_loss = new_stop_loss
            print(f"Trailing SL updated to {stop_loss} (Price: {current_price})")
    
    # When price goes above entry + 60, set stop loss to entry + 40
    if current_price >= entry_price + 60:
        new_stop_loss = entry_price + 40
        if new_stop_loss > stop_loss:
            stop_loss = new_stop_loss
            print(f"Trailing SL updated to {stop_loss} (Price: {current_price})")
    
    # When price goes above entry + 80, set stop loss to entry + 60
    if current_price >= entry_price + 80:
        new_stop_loss = entry_price + 60
        if new_stop_loss > stop_loss:
            stop_loss = new_stop_loss
            print(f"Trailing SL updated to {stop_loss} (Price: {current_price})")
    
    # Continue the pattern for higher prices
    if current_price >= entry_price + 100:
        new_stop_loss = entry_price + 80
        if new_stop_loss > stop_loss:
            stop_loss = new_stop_loss
            print(f"Trailing SL updated to {stop_loss} (Price: {current_price})")

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
    """Enhanced fetch OHLC data and check trading conditions with proper 15-minute logic"""
    global position_active, entry_price, entry_quantity, remaining_quantity, stop_loss, target_1, day_of_week, option_security_id
    
    # Skip if position is already active
    if position_active:
        return
    
    now_ist = datetime.now(IST)
    today_date = now_ist.date()
    
    # Set day of week
    day_of_week = now_ist.strftime('%A')
    
    # Get trading parameters for the day
    trading_params = get_day_trading_params(day_of_week)
    if not trading_params:
        print(f"❌ No trading parameters defined for {day_of_week}")
        return
    
    # Fetch OHLC data
    from_date_obj = IST.localize(datetime(today_date.year, today_date.month, today_date.day,
                                         MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0))
    to_date_obj = now_ist
    
    from_date_str = from_date_obj.strftime("%Y-%m-%d %H:%M:%S")
    to_date_str = to_date_obj.strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        print(f"📊 Fetching OHLC data from {from_date_str} to {to_date_str}")
        
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
            
            # Check if we have enough data (minimum 3 candles)
            if len(df) < 3:
                print(f"⚠️ Not enough data for analysis. Available candles: {len(df)}")
                return
            
            # Get last 3 candles
            B1 = df.iloc[-3]  # 3rd last candle
            B2 = df.iloc[-2]  # 2nd last candle  
            B3 = df.iloc[-1]  # Current/last candle
            
            print(f"📈 Analyzing candles:")
            print(f"B1 (3rd last): O={B1['open']:.2f}, H={B1['high']:.2f}, L={B1['low']:.2f}, C={B1['close']:.2f}")
            print(f"B2 (2nd last): O={B2['open']:.2f}, H={B2['high']:.2f}, L={B2['low']:.2f}, C={B2['close']:.2f}")
            print(f"B3 (current): O={B3['open']:.2f}, H={B3['high']:.2f}, L={B3['low']:.2f}, C={B3['close']:.2f}")
            
            # Check all conditions
            C1 = B1['close'] > B1['open']  # B1 is green candle
            C2 = B2['close'] < B2['open']  # B2 is red candle
            C3 = B2['high'] > B1['high']   # B2 high > B1 high
            C4 = B1['low'] < B2['low']     # B1 low < B2 low
            C5 = (B2['high'] - B2['low']) < 140 if pd.notnull(B2['high']) and pd.notnull(B2['low']) else False
            
            print(f"🔍 Pattern Conditions:")
            print(f"C1 (B1 green): {C1}")
            print(f"C2 (B2 red): {C2}")
            print(f"C3 (B2 high > B1 high): {C3}")
            print(f"C4 (B1 low < B2 low): {C4}")
            print(f"C5 (B2 range < 140): {C5}")
            
            # Check if all conditions are met and no active position
            if C1 and C2 and C3 and C4 and C5:
                print("🎯 ALL CONDITIONS MET! Initiating 15-minute breakout monitoring...")
                
                # ⚡ THIS IS THE KEY: 15-minute breakout monitoring with immediate execution
                if check_breakout_with_market_feed(B1['low']):
                    print("🚀 BREAKOUT CONFIRMED! Executing trade...")
                    
                    # Execute trade immediately
                    execute_trade_after_breakout(trading_params)
                else:
                    print("⏰ No breakout detected within 15 minutes. Pattern expired.")
            else:
                missing_conditions = []
                if not C1: missing_conditions.append("C1 (B1 not green)")
                if not C2: missing_conditions.append("C2 (B2 not red)")
                if not C3: missing_conditions.append("C3 (B2 high not > B1 high)")
                if not C4: missing_conditions.append("C4 (B1 low not < B2 low)")
                if not C5: missing_conditions.append("C5 (B2 range not < 140)")
                
                if missing_conditions:
                    print(f"❌ Pattern incomplete. Missing: {', '.join(missing_conditions)}")
                
        else:
            print("⚠️ No sufficient OHLC data available")
            
    except Exception as e:
        print(f"❌ Error fetching data: {e}")

def execute_trade_after_breakout(trading_params):
    """Execute trade after breakout is confirmed"""
    global position_active, entry_price, entry_quantity, remaining_quantity, stop_loss, target_1, option_security_id
    
    try:
        # Get SENSEX price and calculate strike
        sensex_price = get_sensex_live_price()
        strike_price = convert_to_strike_price_for_sensex(sensex_price)
        print(f"💰 Current SENSEX Price: {sensex_price}, Calculated Strike: {strike_price}")
        
        # Find option security ID
        option_security_id = find_option_security_id(strike_price)
        
        if option_security_id:
            print(f"🎯 Found option security ID: {option_security_id}")
            
            # Start option market feed for this security
            start_option_feed_if_needed(option_security_id)
            
            # Place buy order
            print(f"📈 Placing buy order for {trading_params['quantity']} quantity...")
            order_response = place_buy_order(option_security_id, trading_params['quantity'])
            
            if order_response:
                # Get order ID from response
                order_id = order_response.get('data', {}).get('orderId')
                
                if order_id:
                    print(f"✅ Order placed successfully. Order ID: {order_id}")
                    
                    # Wait a moment for order to execute
                    time_module.sleep(3)
                    
                    # Get entry price from order ID
                    entry_price = get_entry_price_from_order(order_id)
                    
                    if entry_price > 0:
                        # Set position variables
                        position_active = True
                        entry_quantity = trading_params['quantity']
                        remaining_quantity = entry_quantity
                        
                        # Set stop loss and targets using actual entry price
                        stop_loss = entry_price - trading_params['stop_loss']
                        target_1 = entry_price + trading_params['target_1']
                        
                        print(f"🎯 POSITION OPENED SUCCESSFULLY!")
                        print(f"📊 Entry Price: ₹{entry_price}")
                        print(f"🛑 Stop Loss: ₹{stop_loss}")
                        print(f"🎯 Target 1: ₹{target_1}")
                        print(f"📦 Quantity: {entry_quantity}")
                        
                        # Log the trade
                        log_trade("BUY", entry_quantity, entry_price)
                        
                        # Send success alert
                        send_alert(f"🎯 POSITION OPENED!\n"
                                 f"Entry: ₹{entry_price}\n"
                                 f"Quantity: {entry_quantity}\n"
                                 f"Stop Loss: ₹{stop_loss}\n"
                                 f"Target 1: ₹{target_1}\n"
                                 f"Day: {day_of_week}")
                        
                    else:
                        print("❌ Could not get entry price from order")
                        send_alert("❌ Trade execution failed - Could not get entry price")
                else:
                    print("❌ Could not get order ID from response")
                    send_alert("❌ Trade execution failed - No order ID")
            else:
                print("❌ Buy order failed")
                send_alert("❌ Buy order placement failed")
        else:
            print("❌ Could not find option security ID")
            send_alert("❌ Could not find option for trading")
            
    except Exception as e:
        print(f"❌ Error executing trade: {e}")
        send_alert(f"❌ Trade execution error: {e}")

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

def is_candle_complete():
    """Check if current 5-minute candle is complete"""
    now = datetime.now(IST)
    minutes = now.minute
    
    # 5-minute candles complete at: 15, 20, 25, 30, 35, 40, 45, 50, 55, 00, 05, 10
    # Wait at least 30 seconds after candle completion for data to be available
    seconds_buffer = 30
    
    # Check if we're past the candle completion time + buffer
    if minutes % 5 == 0:  # Candle just completed (00, 05, 10, 15, etc.)
        return now.second >= seconds_buffer
    else:
        return False

def get_market_open_candle_time():
    """Get the time when we should have at least 2 complete 5-minute candles for analysis"""
    now = datetime.now(IST)
    today = now.date()
    
    # Market opens at 9:15, so we need:
    # 9:15-9:20 (1st candle)
    # 9:20-9:25 (2nd candle) 
    # Data available after 9:25 + 30 seconds buffer
    
    required_time = IST.localize(datetime.combine(today, time(9, 25, 30)))
    return required_time

def wait_for_sufficient_data():
    """Wait until we have sufficient candle data (2 candles for analysis)"""
    required_time = get_market_open_candle_time()
    now = datetime.now(IST)
    
    if now < required_time:
        wait_seconds = (required_time - now).total_seconds()
        print(f"Waiting {wait_seconds:.0f} seconds for sufficient candle data...")
        time_module.sleep(wait_seconds)

# Add this in your run_trading_strategy function after wait_for_market_open():

def run_trading_strategy():
    """Enhanced main function to run the trading strategy"""
    global market_feed_thread
    
    print("*** Starting Enhanced Trading Strategy ***")
    
    # Download master CSV at startup
    download_master_csv()

    # Try alternative market feed handler first
    print("Starting alternative market feed handler...")
    market_feed_thread = Thread(target=alternative_market_feed_handler, daemon=True)
    market_feed_thread.start()
    print("Market feed thread started")

    # Wait for initialization with longer timeout
    if not wait_for_market_feed_initialization(timeout_seconds=60):
        print("Failed to initialize market feed, trying original method...")
        
        # Stop current thread and try original method
        market_feed_thread = Thread(target=market_feed_handler, daemon=True)
        market_feed_thread.start()
        wait_for_market_feed_initialization(timeout_seconds=60)

    # Wait for market to open
    wait_for_market_open()
    
    print("\n*** Market is open. Starting trading strategy... ***")
    
    # Rest of your existing code continues...
    while True:
        now_in_loop = datetime.now(IST)
        current_time_of_day_in_loop = now_in_loop.time()
        market_close_time = time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, 0)
        
        if current_time_of_day_in_loop > market_close_time:
            print("\n*** Market has closed for the day. ***")
            
            # Force close any open positions at market close
            if position_active:
                print("MARKET CLOSE: Force closing position")
                emergency_close_position()
            
            # Generate daily report
            generate_daily_report()
            
            # Wait for next market open
            wait_for_market_open()
            print("\n*** Market is open again. Resuming trading... ***")
            continue
        
        try:
            fetch_and_check_conditions()
            
            # If position is active, monitor more frequently
            if position_active:
                manage_position()
                time_module.sleep(10)  # Check every 10 seconds when position is active
            else:
                time_module.sleep(30)  # Check every 30 seconds when no position
                
        except Exception as e:
            print(f"Error in main loop: {e}")
            if position_active:
                print("Position active during error - attempting emergency close")
                emergency_close_position()
            print("Continuing with next iteration...")
            time_module.sleep(60)  # Wait longer after error
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