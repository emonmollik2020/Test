# exchange_helper.py
import asyncio
import ccxt.pro as ccxtpro
import pandas as pd
import time
from config import SYMBOL

class ExchangeHelper:
    def __init__(self):
        # পুনরায় বিটগেট এক্সচেঞ্জ ড্রাইভ সচল করা হলো
        self.exchange = ccxtpro.bitget({'enableRateLimit': True})

    # ফিক্সড: params=None প্যারামিটার যুক্ত করা হলো যাতে 'endTime' সফলভাবে এক্সচেঞ্জে পাঠানো যায়
    async def fetch_ohlcv_strict(self, symbol, timeframe, since=None, limit=None, params=None):
        if params is None:
            params = {}
        return await self.exchange.fetch_ohlcv(symbol, timeframe, since, limit, params)

    async def fetch_historical_ohlcv_padded(self, symbol, timeframe, limit_days=90):
        """
        বিটগেট এপিআই-এর ২০০ লিমিটের সীমাবদ্ধতা এড়িয়ে কোনো প্রকার গ্যাপ বা ফাঁকা অংশ ছাড়া 
        ডাইনামিক পেজিনেশনের মাধ্যমে সম্পূর্ণ ৯০ দিনের ১৫-মিনিটের অবিচ্ছিন্ন ক্যান্ডেলস্টিক ডাটা নিয়ে আসে।
        """
        since_ms = int((time.time() - (limit_days * 24 * 60 * 60)) * 1000)
        all_candles = []
        end_time_ms = int(time.time() * 1000)
        
        print(f"Fetching {limit_days} days of historical OHLCV for {symbol} on {timeframe}...")
        
        while end_time_ms > since_ms:
            try:
                # বিটগেট v2 অনুযায়ী ডাইনামিক পেজিনেশন
                params = {'endTime': end_time_ms}
                batch = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=200, params=params)
                
                if not batch:
                    break
                    
                all_candles.extend(batch)
                
                # ফেরত আসা ব্যাচের সবচেয়ে পুরোনো ক্যান্ডেলের টাইমস্ট্যাম্প
                oldest_ts = batch[0][0]
                
                # লুপ লক প্রতিরোধ করার নিরাপত্তা গার্ড
                if oldest_ts >= end_time_ms:
                    break
                    
                # পরবর্তী পেজের জন্য ১ মিলিসেকেন্ড কমিয়ে এগোই
                end_time_ms = oldest_ts - 1
                await asyncio.sleep(0.1) # রেট লিমিট প্রটেকশন
                
            except Exception as e:
                print(f"Error in historical fetch pagination: {e}")
                await asyncio.sleep(2)
                
        if all_candles:
            df_all = pd.DataFrame(all_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df_all = df_all.drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)
            return df_all.values.tolist()
        return []

    async def watch_ohlcv(self, symbol, timeframe, limit=None):
        try:
            ohlcv = await self.exchange.watch_ohlcv(symbol, timeframe, limit=limit)
            return ohlcv
        except Exception as e:
            print(f"Error watching WebSocket OHLCV: {e}")
            await asyncio.sleep(2)
            return await self.fetch_ohlcv_strict(symbol, timeframe, limit=limit)

    async def watch_ticker(self, symbol):
        try:
            ticker = await self.exchange.watch_ticker(symbol)
            return ticker
        except Exception as e:
            print(f"Error watching WebSocket Ticker: {e}")
            await asyncio.sleep(2)
            return await self.exchange.fetch_ticker(symbol)

    async def close(self):
        await self.exchange.close()

# Indentation ফিক্সড: মডিউল লেভেলে অবজেক্ট ডিক্লেয়ারেশন
exchange_helper = ExchangeHelper()
