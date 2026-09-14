import pandas as pd
from datetime import datetime, timedelta, time
import time as time_module  # Rename the time module import to avoid conflict
from dhanhq import dhanhq
from dhanhq import DhanContext, dhanhq
import pytz  # Add this import for timezone handling
import pdb

# --- 1. Your Dhan API Credentials ---
# IMPORTANT: Replace with your actual Client ID and Access Token

CLIENT_ID = "1100996819"  # Use your actual Client ID here
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzUzMDgzOTg2LCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwMDk5NjgxOSJ9.RSoA1tJmFIFGTB0-rHrykeu9am3H1LGe1YX4i4QiBKWE5RQW-W5Xm5H_a2ypncYdYpX6OtG2ASBRMUU7VeC4yA" # Use your actual Access Token here

# --- 2. Initialize the DhanHQ client ---
try:
    dhan_context = DhanContext(CLIENT_ID, ACCESS_TOKEN)
    dhan = dhanhq(dhan_context)

    print("DhanHQ client initialized successfully.")

except Exception as e:
    print(f"Error initializing DhanHQ client: {e}")
    exit() # Exit if client cannot be initialized

# --- 3. Define timezone ---
IST = pytz.timezone('Asia/Kolkata')

# --- 4. Define Market Trading Hours (IST) ---
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# --- 5. Define known 2025 NSE/BSE Market Holidays (Weekdays only) ---
# This list helps the script recognize non-trading days
MARKET_HOLIDAYS_2025 = [
    datetime(2025, 2, 26).date(),  # Maha Shivaratri (Wednesday)
    datetime(2025, 3, 14).date(),  # Holi (Friday)
    datetime(2025, 3, 31).date(),  # Eid-Ul-Fitr (Monday)
    datetime(2025, 4, 10).date(),  # Mahavir Jayanti (Thursday)
    datetime(2025, 4, 14).date(),  # Dr. Baba Saheb Ambedkar Jayanti (Monday)
    datetime(2025, 4, 18).date(),  # Good Friday (Friday)
    datetime(2025, 5, 1).date(),   # Maharashtra Day (Thursday)
    datetime(2025, 8, 15).date(),  # Independence Day (Friday)
    datetime(2025, 8, 27).date(),  # Ganesh Chaturthi (Wednesday)
    datetime(2025, 10, 2).date(),  # Mahatma Gandhi Jayanti (Thursday)
    datetime(2025, 10, 21).date(), # Diwali - Laxmi Pujan (Tuesday) - Muhurat Trading
    datetime(2025, 10, 22).date(), # Diwali - Balipratipada (Wednesday)
    datetime(2025, 11, 5).date(),  # Gurunanak Jayanti (Wednesday)
    datetime(2025, 12, 25).date(), # Christmas (Thursday)
]

def is_trading_day(date_obj):
    """
    Checks if a given date is a trading day (weekday and not a market holiday).
    Returns True if it's a trading day, False otherwise.
    """
    # 5 for Saturday, 6 for Sunday
    if date_obj.weekday() >= 5:
        return False
    # Check against known market holidays
    if date_obj in MARKET_HOLIDAYS_2025:
        return False
    return True

# --- 6. Define Instrument Parameters for NIFTY 50 Index ---
SECURITY_ID = "51"           # Security ID for NIFTY 50 Index
EXCHANGE_SEGMENT = "IDX_I"   # For Indices
INSTRUMENT_TYPE = "INDEX"    # Specifies it's an Index
INTERVAL = 5                 # 5-minute candles


def fetch_and_print_live_ohlc():
    """
    Fetches and prints 5-minute OHLC data for the current trading day
    from market open (9:15 AM IST) up to the current running time (IST).
    """
    now_ist = datetime.now(IST)  # Get current IST time with timezone
    today_date = now_ist.date()

    # Automatically set from_date to 9:15 AM IST of the current day
    from_date_obj = IST.localize(datetime(today_date.year, today_date.month, today_date.day,
                                         MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0))
    
    # Automatically set to_date to the current running time (IST)
    to_date_obj = now_ist

    from_date_str = from_date_obj.strftime("%Y-%m-%d %H:%M:%S")
    to_date_str = to_date_obj.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\nRequesting data for current trading day: {from_date_str} to: {to_date_str}")

    
    try:
        intraday_data_response = dhan.intraday_minute_data(
            security_id=SECURITY_ID,
            exchange_segment=EXCHANGE_SEGMENT,
            instrument_type=INSTRUMENT_TYPE,
            interval=INTERVAL,
            from_date=from_date_str,
            to_date=to_date_str
        )

        # Check if the API call was successful and returned data
        if intraday_data_response and intraday_data_response.get('status') == 'success' and \
           intraday_data_response.get('data') and \
           len(intraday_data_response['data'].get('open', [])) > 0:
            
            ohlc_data = intraday_data_response['data']

            # Convert to Pandas DataFrame for a clear, tabular format
            df = pd.DataFrame({
                'timestamp': ohlc_data['timestamp'],
                'open': ohlc_data['open'],
                'high': ohlc_data['high'],
                'low': ohlc_data['low'],
                'close': ohlc_data['close'],
                'volume': ohlc_data['volume']
            })

            # Convert timestamp (Epoch time in UTC) to IST datetime
            # First convert to UTC, then to IST
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.tz_convert(IST)
            df.set_index('datetime', inplace=True)
            
            # Drop the original 'timestamp' column as 'datetime' is now the index
            df.drop('timestamp', axis=1, inplace=True)

            print("\n--- Fetched 5-minute OHLC Data (Formatted in IST) ---")
            print(df) # Print the entire DataFrame
            print(f"\nTotal {len(df)} {INTERVAL}-minute candles retrieved for the current period.")
        else:
            print("No intraday minute data found for the given parameters or invalid response. "
                  "This might happen if no 5-minute candles have completed yet today, "
                  "or if your Data API subscription isn't active.")

    except Exception as e:
        print(f"Error fetching intraday minute data: {e}")

def wait_for_market_open():
    """
    Waits until market opens at 9:15 AM IST on a trading day.
    """
    while True:
        now_ist = datetime.now(IST)  # Get current IST time with timezone
        today_date = now_ist.date()
        current_time_of_day = now_ist.time()
        
        # Check if today is a trading day
        if not is_trading_day(today_date):
            # Calculate next trading day
            next_day = today_date + timedelta(days=1)
            while not is_trading_day(next_day):
                next_day += timedelta(days=1)
            
            # Calculate time until next trading day at 9:15 AM
            next_market_open = IST.localize(datetime.combine(next_day, time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0)))
            wait_seconds = (next_market_open - now_ist).total_seconds()
            
            print(f"Today is not a trading day. Next trading day: {next_day.strftime('%Y-%m-%d')} at 9:15 AM")
            print(f"Waiting {wait_seconds:.0f} seconds until market opens...")
            time_module.sleep(min(wait_seconds, 3600))  # Sleep for max 1 hour at a time
            continue
        
        # If it's a trading day, check if market is open
        market_open_time = time(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0)
        
        if current_time_of_day >= market_open_time:
            print(f"Market is open! Current time: {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
            break
        else:
            # Calculate seconds until market opens today
            market_open_today = IST.localize(datetime.combine(today_date, market_open_time))
            wait_seconds = (market_open_today - now_ist).total_seconds()
            
            print(f"Market opens in {wait_seconds:.0f} seconds at {market_open_time.strftime('%H:%M')}...")
            time_module.sleep(min(wait_seconds, 60))  # Check every minute

# --- 7. Main execution block for Live Market Data ---
def run_live_data_fetcher():
    """
    Main function to run the live market data fetcher, handling market hours and holidays.
    """
    print("*** Starting Live Market Data Fetcher ***")
    
    # Wait for market to open if it's not open yet
    wait_for_market_open()
    
    print("\n*** Market is open. Starting 5-minute OHLC data fetching... ***")
    
    while True:
        # Re-check current time at the start of each loop iteration
        now_in_loop = datetime.now(IST)  # Get current IST time with timezone
        current_time_of_day_in_loop = now_in_loop.time()
        market_close_time = time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, 0)

        if current_time_of_day_in_loop > market_close_time:
            print("\n*** Market has closed for the day. Stopping data fetching. ***")
            print("*** Script will restart tomorrow at market open. ***")
            # Wait for next market open
            wait_for_market_open()
            print("\n*** Market is open again. Resuming data fetching... ***")
            continue

        try:
            fetch_and_print_live_ohlc() # Fetch and print the latest data
        except Exception as e:
            print(f"Error during data fetch: {e}")
            print("Continuing with next iteration...")

        # Wait for slightly more than 5 minutes before polling again
        # This ensures the next 5-minute candle has potentially closed and is available.
        wait_time_seconds = 5 * 60 + 10 # 5 minutes and 10 seconds
        print(f"\nWaiting {wait_time_seconds} seconds before next fetch...")
        time_module.sleep(wait_time_seconds)

# Run the main function
if __name__ == "__main__":
    run_live_data_fetcher()
