# data_loader.py (ব্যান্ডউইথ সাশ্রয়ী ও শুধুমাত্র গ্যাপ-ফিলিং সংস্করণ)
import asyncio
import time
import pandas as pd
from config import SYMBOL, DB_ENABLED, state_manager
from database import engine
from exchange_helper import exchange_helper

async def bootstrap_or_backfill_sol():
    if not DB_ENABLED:
        return

    try:
        # ১. ডাটাবেস থেকে সর্বশেষ ক্যান্ডেল টাইমস্ট্যাম্প বের করা
        last_ts_df = pd.read_sql("SELECT t FROM sol_15m_history ORDER BY t DESC LIMIT 1", engine)
        
        if not last_ts_df.empty:
            last_db_ts = int(last_ts_df.iloc[0]['t'])
            now_ms = int(time.time() * 1000)
            
            # যদি ১৫ মিনিটের চেয়ে বেশি গ্যাপ থাকে, তবেই সিঙ্ক হবে
            if (now_ms - last_db_ts) > 15 * 60 * 1000:
                print(f"গ্যাপ পাওয়া গেছে! সিঙ্ক শুরু হচ্ছে: {last_db_ts} থেকে...", flush=True)
                
                missing_candles = await exchange_helper.fetch_ohlcv_strict(
                    SYMBOL, '15m', since=last_db_ts + 1, limit=1000
                )
                
                if missing_candles:
                    df_missing = pd.DataFrame(missing_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                    df_missing.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                    print(f"গ্যাপ পূরণ সম্পন্ন! {len(df_missing)} টি নতুন ক্যান্ডেল যোগ করা হয়েছে।", flush=True)
            else:
                print("ডাটাবেস আপ-টু-ডেট আছে, গ্যাপ নেই।", flush=True)
        else:
            # ডাটাবেস খালি থাকলে প্রাথমিক হিস্ট্রি লোড (এটি শুধু প্রথমবার হবে)
            print("ডাটাবেস খালি! প্রাথমিক সিঙ্ক শুরু হচ্ছে...", flush=True)
            # এখানে আপনার আগের পূর্ণাঙ্গ লোডার লজিকটি রাখা যেতে পারে অথবা এটি বাদ দিয়ে খালি রাখা যায়
            
        # সবশেষে পুরো ডাটাফ্রেম মেমোরিতে লোড করা (শুধুমাত্র প্রয়োজনীয় অংশ)
        state_manager.global_df = pd.read_sql("SELECT * FROM sol_15m_history ORDER BY t ASC", engine)
        
    except Exception as e:
        print(f"ডাটাবেস সিঙ্ক এরর: {e}", flush=True)
