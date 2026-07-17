# market_watcher.py (রিয়েল-টাইম লাইভ প্রাইস ও ট্রেন্ড ট্র্যাকার)
import asyncio
import pandas as pd
import ta
from datetime import datetime
from config import SYMBOL, LEVERAGE, state_manager
from database import safe_update_live_state
from exchange_helper import exchange_helper

# মডিউল-লেভেল ট্র্যাকার গ্লোবাল ভেরিয়েবলসমূহ
global_btc_bullish = True
global_btc_bearish = False
global_btc_price = 0.0
global_btc_e20 = 0.0

async def sol_live_ticker_loop():
    """লাইভ সোল প্রাইস ট্র্যাকার লুপ"""
    while True:
        try:
            ticker = await exchange_helper.watch_ticker(SYMBOL)
            p = float(ticker['last'])
            
            cur = state_manager.get()
            updates = {"price": round(p, 2)}
            
            if cur.get("in_position", False):
                entry_p = cur.get("entry_price", 0.0)
                pos_size_usd = cur.get("pos_size", 0.0)
                position_type = cur.get("position_type", "NONE")
                
                if entry_p > 0:
                    if position_type == "LONG":
                        l_pnl = ((p / entry_p) - 1) * 100 * LEVERAGE
                        l_val = pos_size_usd * ((p / entry_p) - 1)
                    else: 
                        l_pnl = (1 - (p / entry_p)) * 100 * LEVERAGE
                        l_val = pos_size_usd * (1 - (p / entry_p))
                else:
                    l_pnl = 0.0
                    l_val = 0.0
                    
                updates["live_pnl_pct"] = round(l_pnl, 2)
                updates["live_pnl_val"] = round(l_val, 2)
            
            await safe_update_live_state(updates)
        except Exception as e:
            print(f"Live Ticker Loop Warning: {e}", flush=True)
            await asyncio.sleep(1)

async def btc_ema_update_loop():
    """বিটিসি ১ ঘণ্টা চার্টের EMA ২০ ব্যাকগ্রাউন্ড লুপ"""
    global global_btc_e20
    while True:
        try:
            candles = await exchange_helper.fetch_ohlcv_strict("BTC/USDT:USDT", '1h', limit=50)
            if candles:
                df_btc = pd.DataFrame(candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                ema_series = ta.trend.ema_indicator(df_btc['c'], 20)
                if not ema_series.empty:
                    global_btc_e20 = float(ema_series.iloc[-1])
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] BTC 1h EMA 20 সফলভাবে আপডেট হয়েছে: ${global_btc_e20:.2f}", flush=True)
            await asyncio.sleep(300)
        except Exception as e:
            print(f"BTC EMA Update Loop Warning: {e}", flush=True)
            await asyncio.sleep(10)

async def btc_ticker_loop():
    """লাইভ বিটিসি প্রাইস ও ট্রেন্ড ট্র্যাকার লুপ"""
    global global_btc_bullish, global_btc_bearish, global_btc_price, global_btc_e20
    while True:
        try:
            ticker_btc = await exchange_helper.watch_ticker("BTC/USDT:USDT")
            btc_p = float(ticker_btc['last'])
            global_btc_price = btc_p
            
            if global_btc_e20 > 0.0:
                global_btc_bullish = btc_p > global_btc_e20
                global_btc_bearish = btc_p < global_btc_e20
            else:
                global_btc_bullish = True
                global_btc_bearish = False
        except Exception as e:
            print(f"BTC Ticker Loop Warning: {e}", flush=True)
            await asyncio.sleep(2)
