import ta.momentum
from SmartApi import SmartConnect
import pyotp
from logzero import logger
import ta
import pandas as pd
from collections import deque
from datetime import datetime, timedelta
import time
import threading

# === Credentials ===
api_key = "XezA2pz7"
username = 'M464983'
pwd = '1980'
token = "FQO6KWBEHR3PYGV4UFPHFIYAFY"

# === Auth ===
smartApi = SmartConnect(api_key)
totp = pyotp.TOTP(token).now()
data = smartApi.generateSession(username, pwd, totp)

if not data['status']:
    logger.error(data)
    exit()

authToken = data['data']['jwtToken']
refreshToken = data['data']['refreshToken']
feedToken = smartApi.getfeedToken()

AUTH_TOKEN = authToken
API_KEY = api_key
CLIENT_CODE = username
FEED_TOKEN = feedToken

# === Index Tokens ===
index_tokens = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
    "FINNIFTY": "99926037"
}

candles = {symbol: deque(maxlen=3) for symbol in index_tokens.keys()}

# === WebSocket ===
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
sws = SmartWebSocketV2(AUTH_TOKEN, API_KEY, CLIENT_CODE, FEED_TOKEN)

# === Flag to control rate limit ===
last_fetch_time = {}

def fetch_latest_5m_candle(symbol):
    now = datetime.now()
    if symbol in last_fetch_time:
        # Enforce 5-minute gap
        if (now - last_fetch_time[symbol]).seconds < 300:
            return None

    try:
        symbol_token = index_tokens[symbol]
        end_time = now
        start_time = end_time - timedelta(minutes=15)

        historicParam = {
            "exchange": "NSE",
            "symboltoken": symbol_token,
            "interval": "FIVE_MINUTE",
            "fromdate": start_time.strftime("%Y-%m-%d %H:%M"),
            "todate": end_time.strftime("%Y-%m-%d %H:%M")
        }

        response = smartApi.getCandleData(historicParam)
        if response['status']:
            last_fetch_time[symbol] = now
            data = response['data'][-1]
            return {
                'start': data[0],
                'open': float(data[1]),
                'high': float(data[2]),
                'low': float(data[3]),
                'close': float(data[4])
            }
    except Exception as e:
        logger.error(f"Error fetching candle for {symbol}: {e}")
    return None

def check_inside_bar(symbol):
    dq = candles[symbol]
    if len(dq) < 3:
        return

    c1, c2, live = dq[0], dq[1], dq[2]
    green_red = (c1['close'] > c1['open'] and c2['close'] < c2['open']) or \
                (c1['close'] < c1['open'] and c2['close'] > c2['open'])

    if green_red:
        if c2['high'] < c1['high'] and c2['low'] > c1['low']:
            if live['close'] > c2['high']:
                print(f"{symbol}: BUY CALL OPTION")
            elif live['close'] < c2['low']:
                print(f"{symbol}: BUY PUT OPTION")
        elif c1['high'] < c2['high'] and c1['low'] > c2['low']:
            if live['close'] > c1['high']:
                print(f"{symbol}: BUY CALL OPTION")
            elif live['close'] < c1['low']:
                print(f"{symbol}: BUY PUT OPTION")

def update_candles_periodically():
    while True:
        for symbol in index_tokens:
            candle = fetch_latest_5m_candle(symbol)
            if candle:
                candles[symbol].append(candle)
                check_inside_bar(symbol)
        time.sleep(60)  # check once per minute for new 5m candle

def on_data(wsapp, msg):
    pass  # WebSocket just stays alive for now

def on_open(wsapp):
    logger.info("WebSocket connection established")
    sws.subscribe("index_sub", 1, [{"exchangeType": 1, "tokens": list(index_tokens.values())}])

def on_error(wsapp, error):
    logger.error(f"WebSocket error: {error}")

def on_close(wsapp):
    logger.info("WebSocket closed")

sws.on_open = on_open
sws.on_data = on_data
sws.on_error = on_error
sws.on_close = on_close

# === Start WebSocket ===
logger.info("Starting WebSocket...")
sws.connect()

# === Start background thread ===
thread = threading.Thread(target=update_candles_periodically, daemon=True)
thread.start()

# === Keep alive ===
try:
    while True:
        time.sleep(300)
except KeyboardInterrupt:
    sws.close_connection()
    logger.info("Stopped.")
