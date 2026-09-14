from dhanhq import DhanContext, MarketFeed
import time

# Your credentials
CLIENT_ID = "1100996819"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzU4MDU1ODU0LCJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJ3ZWJob29rVXJsIjoiIiwiZGhhbkNsaWVudElkIjoiMTEwMDk5NjgxOSJ9.iYv1pUDj6699diJGfS5pW1lfwT5SUEuFYtFSVsWSju4C66XsiCUjF5TAVIVWvGcfHdJLxFFkXLshC0aXOR5Cow"

# Initialize DhanHQ context
dhan_context = DhanContext( CLIENT_ID, ACCESS_TOKEN)

# Define instruments to subscribe to
# Format: (exchange, security_id, subscription_type)
instruments = [ (MarketFeed.BSE_FNO, "846738", MarketFeed.Ticker)]

version = "v2"

try:
    data = MarketFeed(dhan_context, instruments, version) 
    data.run_forever()
    
    while True:
        response = data.get_data()
        print(response) 
except Exception as e:
    print(f"Error: {e}")
    print("Please check your credentials and internet connection.")
