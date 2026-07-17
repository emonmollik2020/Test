# data_loader.py (হিস্টোরিক্যাল ডেটা লোডার ও সিঙ্ক ইঞ্জিন)
import asyncio
import os
import time
import pandas as pd
from config import SYMBOL, HISTORY_FILE, DB_ENABLED, MAX_CANDLES_TO_KEEP, state_manager
from database import engine, load_state, safe_save_state
from exchange_helper import exchange_helper

async def bootstrap_or_backfill_sol():
    now_ms = int(time.time() * 1000)
    df = None
    
    if DB_ENABLED:
        try:
            df = pd.read_sql("SELECT * FROM sol_15m_history ORDER BY t ASC", engine)
            if not df.empty:
                print(f"Loaded existing history from Supabase. Rows: {len(df)}", flush=True)
        except Exception as e:
            print(f"Database read error: {e}", flush=True)
            df = None

    if (df is None or df.empty) and os.path.exists(HISTORY_FILE):
        try:
            df = pd.read_csv(HISTORY_FILE)
            if DB_ENABLED and not df.empty:
                df.to_sql('sol_15m_history', engine, if_exists='append', index=False)
        except Exception:
            df = None

    if df is None or df.empty or len(df) < 2500:
        print("Initializing 90-Days historical async bootstrap from Bitget...", flush=True)
        all_candles = []
        total_duration_ms = 90 * 24 * 60 * 60 * 1000
        start_time_ms = now_ms - total_duration_ms
        end_time_ms = now_ms
        
        while end_time_ms > start_time_ms:
            elapsed_ms = now_ms - end_time_ms
            progress_pct = min(100, max(1, int((elapsed_ms / total_duration_ms) * 100)))
            progress_msg = f"বিগত ৯০ দিনের হিস্ট্রি ডেটা ডাউনলোড হচ্ছে... {progress_pct}% সম্পন্ন"
            
            cur = load_state(force_reload=True)
            cur["wait_reason"] = progress_msg
            remaining_ms = end_time_ms - start_time_ms
            est_sec = max(5, int((remaining_ms / (24 * 60 * 60 * 1000)) * 0.8))
            cur["estimated_time"] = f"প্রায় {est_sec} সেকেন্ড বাকি"
            await safe_save_state(cur)
            print(f"[{datetime.now().strftime('%H:%M:%S') if 'datetime' in globals() else ''}] {progress_msg}", flush=True)
            
            try:
                params = {'endTime': end_time_ms}
                candles = await exchange_helper.fetch_ohlcv_strict(SYMBOL, '15m', limit=200, params=params)
                if not candles:
                    break
                all_candles.extend(candles)
                
                oldest_ts = candles[0][0]
                if oldest_ts >= end_time_ms:
                    break
                    
                end_time_ms = oldest_ts - 1
            except Exception as e:
                print(f"Bootstrap Fetch Warning: {e}", flush=True)
                await asyncio.sleep(2)
                
            await asyncio.sleep(0.15)
                
        if all_candles:
            df = pd.DataFrame(all_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df = df.drop_duplicates(subset=['t'], keep='last').sort_values('t').reset_index(drop=True)
            df.to_csv(HISTORY_FILE, index=False)
            
            if DB_ENABLED:
                try:
                    from sqlalchemy import text
                    with engine.begin() as conn:
                        conn.execute(text("TRUNCATE TABLE sol_15m_history;"))
                    df.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                    print("Successfully saved historical data to Supabase.", flush=True)
                except Exception as e:
                    print(f"DB Insert Warning: {e}", flush=True)
                    pass
        else:
            df = pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'v'])
    else:
        last_ts = int(df['t'].iloc[-1])
        if now_ms - last_ts > 15 * 60 * 1000:
            print("Syncing missing gaps for historical history...", flush=True)
            missing_candles = []
            since = last_ts + 1
            while since < now_ms:
                try:
                    candles = await exchange_helper.fetch_ohlcv_strict(SYMBOL, '15m', since=since, limit=1000)
                    if not candles:
                        break
                    missing_candles.extend(candles)
                    since = candles[-1][0] + 1
                    await asyncio.sleep(0.15)
                except Exception:
                    await asyncio.sleep(1)
                    break
            
            if missing_candles:
                df_missing = pd.DataFrame(missing_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                df = pd.concat([df, df_missing]).drop_duplicates(subset=['t'], keep='last').sort_values('t').reset_index(drop=True)
                
                if len(df) > MAX_CANDLES_TO_KEEP:
                    df_csv = df.iloc[-MAX_CANDLES_TO_KEEP:]
                else:
                    df_csv = df.copy()
                df_csv.to_csv(HISTORY_FILE, index=False)
                
                if DB_ENABLED:
                    try:
                        df_missing.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                    except Exception:
                        pass
                        
    state_manager.global_df = df.copy()
    return df
