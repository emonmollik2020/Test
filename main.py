# =====================================================================
# SECTION 1: প্রয়োজনীয় লাইব্রেরি ইম্পোর্ট ও গ্লোবাল সেটিংস
# =====================================================================
import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
import math
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

# ফিউচার্স পেয়ার এবং সেটিংস
SYMBOL = "SOL/USDT:USDT"
STATE_FILE = "bot_state.json"
HISTORY_FILE = "sol_15m_history.csv"
INITIAL_FUND = 100.0
MAX_CANDLES_TO_KEEP = 5000  # সর্বোচ্চ ৫০ দিনের হিস্ট্রি লিমিট

# ১০x লিভারেজ এবং প্রফেশনাল রিস্ক পার্সেন্টেজ
LEVERAGE = 10
RISK_FRACTION = 0.02 # প্রতিটি ট্রেডে মোট ফান্ডের সর্বোচ্চ ২% রিস্ক নেবে

# সুইং ও ট্রেন্ড ট্রেডিংয়ের জন্য স্ট্যান্ডার্ড স্টপ লস ও টেক প্রফিট
DEF_TP = 0.035  
DEF_SL = 0.020  

# থ্রেড লক
STATE_LOCK = threading.Lock()

# ইন-মেমোরি লোকাল ক্যাশ গ্লোবাল ভ্যারিয়েবল
CANDLE_CACHE_BTC = []

# ইন-মেমোরি গ্লোবাল ডাটাফ্রেম ক্যাশ (চার্ট দ্রুত লোড করার জন্য ফাইললেস মেমোরি সিস্টেম)
GLOBAL_DF_LOCK = threading.Lock()
GLOBAL_DF = pd.DataFrame()

# =====================================================================
# SECTION 1.5: ক্লাউড ডাটাবেস (Supabase) ও এক্সচেঞ্জ সেটিংস
# =====================================================================
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
    db_enabled = True
else:
    engine = None
    db_enabled = False
    print("Warning: DATABASE_URL not found. Running in local file backup mode.")

# মূল এক্সচেঞ্জ কানেকশন (কঠোরভাবে শুধুমাত্র বিটগেট)
exchange = ccxt.bitget({'enableRateLimit': True})

# ডিফল্ট স্টেট
DEFAULT_STATE = {
    "price": 0.0,
    "balance": INITIAL_FUND,
    "total_pnl": 0.0,
    "last_update": "...",
    "trades": 0,
    "win_rate": 0,
    "best": 0.0,
    "worst": 0.0,
    "last_action": "---",
    "in_position": False,
    "position_type": "NONE", 
    "peak_p": 0.0,            
    "valley_p": 0.0,          
    "live_pnl_pct": 0.0,
    "live_pnl_val": 0.0,
    "entry_price": 0.0,
    "sl_level": 0.0,
    "tp_level": 0.0,
    "pos_size": 0.0,  
    "margin": 0.0,    
    "entry_sl_pct": 0.0, 
    "last_trade_time": 0.0,
    "estimated_time": "লোড হচ্ছে...",
    "pdh": 0.0,         # Previous Day High
    "pdl": 0.0,         # Previous Day Low
    "h4_res": 0.0,      # H4 Resistance
    "h4_sup": 0.0,      # H4 Support
    
    "analysis_15m": {"rsi": 0, "ema20": 0, "ema50": 0, "vwap": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_30m": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_45m": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_1h": {"rsi": 0, "ema20": 0, "ema50": 0, "ema200": 0, "btc_price": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_2h": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_3h": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_4h": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    "analysis_1d": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []},
    
    "confluences": {
        "macro_bullish": False, "btc_bullish": False, "vwap_long": False, "volume_confirmed": False,
        "ema_long": False, "macd_long": False, "bull_signal": False,
        "macro_bearish": False, "btc_bearish": False, "vwap_short": False,
        "ema_short": False, "macd_short": False, "bear_signal": False
    },
    "exit_conditions": { 
        "sl_safe": True, "tp_safe": True, "ema50_safe": True, "rsi_safe": True, "is_breakeven": False
    },
    "wait_reason": "লোড হচ্ছে...",
    "log": [],
    "history": []
}

LAST_LOADED_TIME = 0
CACHED_STATE = DEFAULT_STATE.copy()


# =====================================================================
# SECTION 2: ফাইল ও ডাটাবেস ম্যানেজমেন্ট (হাইব্রিড আর্কিটেকচার)
# =====================================================================
def clean_float(val):
    if val is None or pd.isna(val) or math.isnan(val) or math.isinf(val):
        return 0.0
    return float(val)


def init_db_tables():
    if not db_enabled:
        return
    try:
        with engine.connect() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sol_15m_history (
                t BIGINT PRIMARY KEY,
                o DOUBLE PRECISION,
                h DOUBLE PRECISION,
                l DOUBLE PRECISION,
                c DOUBLE PRECISION,
                v DOUBLE PRECISION
            );
            """))
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                state_data JSONB
            );
            """))
            conn.commit()
        print("Database tables initialized successfully.")
    except Exception as e:
        print(f"Error initializing database tables: {e}")


def save_state(d):
    with STATE_LOCK:
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(d, f)
        except Exception as e:
            print(f"Local file write error: {e}")
            
        if db_enabled:
            try:
                state_json = json.dumps(d)
                with engine.connect() as conn:
                    query = """
                    INSERT INTO bot_state (key, state_data) 
                    VALUES ('current_state', :state_data) 
                    ON CONFLICT (key) 
                    DO UPDATE SET state_data = EXCLUDED.state_data;
                    """
                    conn.execute(text(query), {"state_data": state_json})
                    conn.commit()
            except Exception as e:
                print(f"Cloud state backup error: {e}")


def load_state():
    global LAST_LOADED_TIME, CACHED_STATE
    with STATE_LOCK:
        if CACHED_STATE["price"] > 0.0:
            return CACHED_STATE.copy()
            
        if db_enabled:
            try:
                with engine.connect() as conn:
                    result = conn.execute(text("SELECT state_data FROM bot_state WHERE key = 'current_state'")).fetchone()
                    if result:
                        CACHED_STATE = json.loads(result[0])
                        print("Successfully recovered last bot state from Supabase Cloud.")
                        return CACHED_STATE.copy()
            except Exception as e:
                print(f"Could not load state from Cloud Database: {e}")
            
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    CACHED_STATE = json.load(f)
                return CACHED_STATE.copy()
            except Exception:
                pass
                
        return DEFAULT_STATE.copy()


app = Flask(__name__)


# =====================================================================
# SECTION 2.5: ওএইচএলসিভি ডেটা রিকোয়েস্ট লজিক (কঠোরভাবে শুধুমাত্র বিটগেট)
# =====================================================================
def fetch_ohlcv_strict(symbol, timeframe, since=None, limit=None):
    return exchange.fetch_ohlcv(symbol, timeframe, since, limit)


# =====================================================================
# SECTION 3: ক্যান্ডেলস্টিক প্যাটার্ন ডিটেক্টর
# =====================================================================
def get_advanced_pats(df):
    p = []
    if len(df) < 6:
        return p
        
    c1, c2, c3, c4, c5 = df.iloc[-1], df.iloc[-2], df.iloc[-3], df.iloc[-4], df.iloc[-5]
    
    def info(c):
        body = abs(c['c'] - c['o'])
        total = max(0.001, c['h'] - c['l'])
        u_wick = c['h'] - max(c['c'], c['o'])
        l_wick = min(c['c'], c['o']) - c['l']
        is_green = c['c'] > c['o']
        is_doji = (body / total) < 0.1
        return body, total, u_wick, l_wick, is_green, is_doji
        
    b1, t1, u1, l1, g1, d1 = info(c1)
    b2, t2, u2, l2, g2, d2 = info(c2)
    b3, t3, u3, l3, g3, d3 = info(c3)
    b4, t4, u4, l4, g4, d4 = info(c4)
    b5, t5, u5, l5, g5, d5 = info(c5)

    # বুলিশ প্যাটার্নস (LONG সিগন্যাল)
    if b1 > 0 and l1 >= 2.0 * b1 and u1 <= 0.1 * t1 and not d1: 
        p.append({"n": "হ্যামার \U0001F528", "t": "bull"})
    if b1 > 0 and u1 >= 2.0 * b1 and l1 <= 0.1 * t1 and g1 and not d1: 
        p.append({"n": "ইনভার্টেড হ্যামার \U0001F528", "t": "bull"})
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c'] and b1 > b2: 
        p.append({"n": "বুলিশ এনгалফিং \U0001F4C8", "t": "bull"})
    if not g3 and b2 < (b3 * 0.3) and g1 and c1['c'] > (c3['o'] + c3['c']) / 2 and not d2: 
        p.append({"n": "মর্নিং স্টার \U0001F305", "t": "bull"})
    if not g3 and d2 and g1 and c1['c'] > (c3['o'] + c3['c']) / 2: 
        p.append({"n": "মর্নিং ডোজি স্টার \U0001F305\u271D\uFE0F", "t": "bull"})
    if (b1 / t1) > 0.90 and g1: 
        p.append({"n": "বুলিশ মারুবোজু \U0001F4AA", "t": "bull"})
    if abs(c1['l'] - c2['l']) / max(0.001, c1['l']) < 0.0015 and not g2 and g1: 
        p.append({"n": "টুইজার বটম \U0001F9AA", "t": "bull"})

    # বেয়ারিশ প্যাটার্নস (SHORT সিগন্যাল)
    if b1 > 0 and u1 >= 2.0 * b1 and l1 <= 0.1 * t1 and not g1 and not d1: 
        p.append({"n": "শুটিং স্টার \u2604\uFE0F", "t": "bear"})
    if b1 > 0 and l1 >= 2.0 * b1 and u1 <= 0.1 * t1 and not g1 and not d1: 
        p.append({"n": "হ্যাঙ্গিং ম্যান \U0001F574\uFE0F", "t": "bear"})
    if g2 and not g1 and c1['c'] <= c2['o'] and c1['o'] >= c2['c'] and b1 > b2: 
        p.append({"n": "বেয়ারিশ এনгалফিং \U0001F4C9", "t": "bear"})
    if g3 and b2 < (b3 * 0.3) and not g1 and c1['c'] < (c3['o'] + c3['c']) / 2 and not d2: 
        p.append({"n": "ইভনিং স্টার \U0001F307", "t": "bear"})

    return p

# =====================================================================
# SECTION 3.5: টাইম এস্টিমেটর ফরমেট হেল্পার এবং ক্যাশ মার্জ লজিক
# =====================================================================
def format_seconds_to_bengali(sec):
    sec = int(sec)
    if sec <= 0:
        return "খুব কাছাকাছি"
    weeks = sec // (7 * 24 * 3600)
    sec %= (7 * 24 * 3600)
    days = sec // (24 * 3600)
    sec %= (24 * 3600)
    hours = sec // 3600
    sec %= 3600
    minutes = sec // 60
    seconds = sec % 60
    
    parts = []
    if weeks > 0:
        parts.append(f"{weeks} সপ্তাহ")
    if days > 0:
        parts.append(f"{days} দিন")
    if hours > 0:
        parts.append(f"{hours} ঘণ্টা")
    if minutes > 0:
        parts.append(f"{minutes} মিনিট")
    if seconds > 0 and len(parts) < 2:
        parts.append(f"{seconds} সেকেন্ড")
        
    return " ".join(parts) if parts else "খুব কাছাকাছি"


def merge_ohlcv_cache(cache_list, new_candles, max_len=1000):
    if not cache_list:
        return new_candles[-max_len:]
    cache_dict = {c[0]: c for c in cache_list}
    for c in new_candles:
        cache_dict[c[0]] = c
    sorted_ts = sorted(cache_dict.keys())
    keep_ts = sorted_ts[-max_len:]
    return [cache_dict[ts] for ts in keep_ts]


# =====================================================================
# SECTION 3.6: Supabase চালিত ২.৫ মিনিট অপ্টিমাইজড বুটস্ট্র্যাপ এবং ব্যাকফিল
# =====================================================================
def bootstrap_or_backfill_sol():
    global GLOBAL_DF
    now_ms = int(time.time() * 1000)
    df = None
    
    if db_enabled:
        try:
            df = pd.read_sql("SELECT * FROM sol_15m_history ORDER BY t ASC", engine)
            if not df.empty:
                print(f"Loaded existing history from Supabase Cloud. Rows: {len(df)}")
        except Exception as e:
            print(f"No existing history found in database (or connection error): {e}")
            df = None

    if (df is None or df.empty) and os.path.exists(HISTORY_FILE):
        try:
            df = pd.read_csv(HISTORY_FILE)
            print(f"Loaded backup history from {HISTORY_FILE}. Rows: {len(df)}")
            if db_enabled and not df.empty:
                df.to_sql('sol_15m_history', engine, if_exists='append', index=False)
        except Exception as e:
            print(f"Error reading local backup CSV: {e}")
            df = None

    if df is None or df.empty:
        print("No history found. Initializing optimized 2.5 minutes bootstrap from Bitget...")
        all_candles = []
        total_steps = 10
        chunk_size = 500
        since = now_ms - (45 * 24 * 60 * 60 * 1000)
        
        for step in range(1, total_steps + 1):
            progress_pct = int((step / total_steps) * 100)
            progress_msg = f"হিস্ট্রি ডেটা লোড হচ্ছে... {progress_pct}% সম্পন্ন"
            
            cur = load_state()
            cur["wait_reason"] = progress_msg
            est_sec = (total_steps - step) * 15
            cur["estimated_time"] = f"{est_sec // 60} মিনিট {est_sec % 60} সেকেন্ড বাকি"
            save_state(cur)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {progress_msg}")
            
            try:
                candles = fetch_ohlcv_strict(SYMBOL, '15m', since=since, limit=chunk_size)
                if not candles:
                    break
                all_candles.extend(candles)
                since = candles[-1][0] + 1
            except Exception as e:
                cur = load_state()
                cur["wait_reason"] = f"সংযোগ ত্রুটি: {str(e)[:40]} (বিটগেটের সাথে পুনরায় চেষ্টা চলছে)"
                save_state(cur)
                print(f"Error fetching during bootstrap: {e}")
            
            if step < total_steps:
                time.sleep(15)
                
        if all_candles:
            df = pd.DataFrame(all_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df = df.drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)
            df.to_csv(HISTORY_FILE, index=False)
            
            if db_enabled:
                try:
                    df.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                    print("Saved bootsrapped candles to Supabase Cloud.")
                except Exception as e:
                    print(f"Error saving to database: {e}")
        else:
            df = pd.DataFrame(columns=['t', 'o', 'h', 'l', 'c', 'v'])

    else:
        last_ts = int(df['t'].iloc[-1])
        fifteen_mins_ms = 15 * 60 * 1000
        if now_ms - last_ts > fifteen_mins_ms:
            print("Gaps detected in history. Syncing missing candles...")
            missing_candles = []
            since = last_ts + 1
            while since < now_ms:
                try:
                    candles = fetch_ohlcv_strict(SYMBOL, '15m', since=since, limit=1000)
                    if not candles:
                        break
                    missing_candles.extend(candles)
                    since = candles[-1][0] + 1
                except Exception as e:
                    print(f"Error during backfilling: {e}")
                    break
            
            if missing_candles:
                df_missing = pd.DataFrame(missing_candles, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                df = pd.concat([df, df_missing]).drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)
                if len(df) > MAX_CANDLES_TO_KEEP:
                    df = df.iloc[-MAX_CANDLES_TO_KEEP:]
                
                df.to_csv(HISTORY_FILE, index=False)
                if db_enabled:
                    try:
                        df_missing.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                        print("Synced complete with Supabase Cloud.")
                    except Exception as e:
                        print(f"Database sync error during backfill: {e}")
                        
    if df is not None and not df.empty:
        with GLOBAL_DF_LOCK:
            GLOBAL_DF = df.copy()
                
    return df


# =====================================================================
# SECTION 3.7: টাইমফ্রেম রিস্যাম্পলিং ও ইন্ডিকেটর ক্যালকুলেটর হেল্পারস
# =====================================================================
def resample_tf(df_temp, tf_str):
    df_res = df_temp.resample(tf_str).agg({
        't': 'first', 'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'
    }).dropna().reset_index(drop=True)
    return df_res


def analyze_tf(df_res, original_price):
    if len(df_res) < 2:
        return {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "লোড হচ্ছে...", "pats": []}
    
    rsi_s = ta.momentum.rsi(df_res['c'], window=min(14, len(df_res)-1)).fillna(0)
    e20_s = ta.trend.ema_indicator(df_res['c'], min(20, len(df_res)-1)).fillna(0)
    e50_s = ta.trend.ema_indicator(df_res['c'], min(50, len(df_res)-1)).fillna(0)
    
    rsi_val = rsi_s.iloc[-1] if not rsi_s.empty else 0.0
    e20_val = e20_s.iloc[-1] if not e20_s.empty else 0.0
    e50_val = e50_s.iloc[-1] if not e50_s.empty else 0.0
    
    sig = "বুলিশ ✅" if original_price > e20_val else "বেয়ারিশ ❌"
    pats = get_advanced_pats(df_res)
    
    return {
        "rsi": round(rsi_val, 1),
        "ema20": round(e20_val, 2),
        "ema50": round(e50_val, 2),
        "sig": sig,
        "pats": pats
    }


# =====================================================================
# SECTION 4: টু-ওয়ে ফিউচার্স ট্রেডিং বট ইঞ্জিন (১০x লিভারেজ)
# =====================================================================
def bot_engine():
    global CANDLE_CACHE_BTC, GLOBAL_DF
    
    init_db_tables()
    
    cur_init = load_state()
    total = cur_init.get("trades", 0)
    win_rate = cur_init.get("win_rate", 0)
    wins = int((win_rate / 100.0) * total) if total > 0 else 0
    net_pnl = cur_init.get("total_pnl", 0.0)
    
    position_type = cur_init.get("position_type", "NONE")
    peak_p = cur_init.get("peak_p", 0.0)
    valley_p = cur_init.get("valley_p", 0.0)
    in_pos = cur_init.get("in_position", False)
    entry_p = cur_init.get("entry_price", 0.0)
    last_trade_time = cur_init.get("last_trade_time", 0.0)
    
    COOLDOWN_SECONDS = 900      

    print("Initializing SOL 15m Database...")
    df_sol = bootstrap_or_backfill_sol()

    while True:
        try:
            try:
                raw_sol = fetch_ohlcv_strict(SYMBOL, '15m', limit=2)
                df_new = pd.DataFrame(raw_sol, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                
                if db_enabled:
                    with engine.connect() as conn:
                        timestamps = tuple(df_new['t'].tolist())
                        if len(timestamps) == 1:
                            conn.execute(text(f"DELETE FROM sol_15m_history WHERE t = {timestamps[0]}"))
                        else:
                            conn.execute(text("DELETE FROM sol_15m_history WHERE t IN :timestamps"), {"timestamps": timestamps})
                        conn.commit()
                    df_new.to_sql('sol_15m_history', engine, if_exists='append', index=False)
                
                df_sol = pd.concat([df_sol, df_new]).drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)
                if len(df_sol) > MAX_CANDLES_TO_KEEP:
                    df_sol = df_sol.iloc[-MAX_CANDLES_TO_KEEP:]
                df_sol.to_csv(HISTORY_FILE, index=False)
                
                with GLOBAL_DF_LOCK:
                    GLOBAL_DF = df_sol.copy()
            except Exception as e:
                print(f"Error updating SOL 15m Database: {e}")
                
            df15 = df_sol.copy()
            
            if df15.empty:
                cur = load_state()
                cur["wait_reason"] = "বিটগেট সংযোগ ব্যর্থ! ওএইচএলসিভি ডেটা খালি 🛑"
                save_state(cur)
                time.sleep(10)
                continue
            
            try:
                if not CANDLE_CACHE_BTC:
                    raw_btc = fetch_ohlcv_strict("BTC/USDT:USDT", '15m', limit=100)
                else:
                    raw_btc = fetch_ohlcv_strict("BTC/USDT:USDT", '15m', limit=5)
                CANDLE_CACHE_BTC = merge_ohlcv_cache(CANDLE_CACHE_BTC, raw_btc, max_len=100)
            except Exception as e:
                print(f"Error fetching BTC/USDT live: {e}")
                
            df_btc = pd.DataFrame(CANDLE_CACHE_BTC, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            if df_btc.empty:
                df_btc = pd.DataFrame([[int(time.time()*1000), 0.0, 0.0, 0.0, 0.0, 0.0]], columns=['t', 'o', 'h', 'l', 'c', 'v'])
            
            df15_temp = df15.copy()
            df15_temp['dt'] = pd.to_datetime(df15_temp['t'], unit='ms')
            df15_temp.set_index('dt', inplace=True)
            
            df30m = resample_tf(df15_temp, '30min')
            df45m = resample_tf(df15_temp, '45min')
            df1h = resample_tf(df15_temp, '1h')
            df2h = resample_tf(df15_temp, '2h')
            df3h = resample_tf(df15_temp, '3h')
            df4h = resample_tf(df15_temp, '4h')
            df1d = resample_tf(df15_temp, '1D')
            
            pdh = clean_float(df1d['h'].iloc[-2]) if len(df1d) >= 2 else 0.0
            pdl = clean_float(df1d['l'].iloc[-2]) if len(df1d) >= 2 else 0.0
            h4_res = clean_float(df4h['h'].iloc[-20:].max()) if len(df4h) >= 20 else 0.0
            h4_sup = clean_float(df4h['l'].iloc[-20:].min()) if len(df4h) >= 20 else 0.0
            
            p = df15['c'].iloc[-1]
            high_p = df15['h'].iloc[-1] 
            low_p = df15['l'].iloc[-1]
            
            btc_p = df_btc['c'].iloc[-1]
            btc_e20 = ta.trend.ema_indicator(df_btc['c'], 20).fillna(0).iloc[-1]
            btc_bullish = btc_p > btc_e20  
            btc_bearish = btc_p < btc_e20  
            
            sol_vol_ma = df15['v'].rolling(window=15).mean().fillna(0).iloc[-1]
            sol_current_vol = df15['v'].iloc[-1]
            volume_confirmed = sol_current_vol > (1.2 * sol_vol_ma)
            
            vwap_series = ta.volume.volume_weighted_average_price(high=df15['h'], low=df15['l'], close=df15['c'], volume=df15['v'], window=14)
            vwap = vwap_series.fillna(0).iloc[-1]
            vwap_long_confirmed = p > vwap   
            vwap_short_confirmed = p < vwap  
            
            atr = ta.volatility.average_true_range(high=df15['h'], low=df15['l'], close=df15['c'], window=14).fillna(0).iloc[-1]
            atr_pct = atr / p
            dynamic_tp_pct = max(0.015, min(0.060, 2.5 * atr_pct))  
            dynamic_sl_pct = max(0.010, min(0.035, 1.5 * atr_pct))  
            
            r15_series = ta.momentum.rsi(df15['c'], window=14).fillna(0)
            e20_series = ta.trend.ema_indicator(df15['c'], 20).fillna(0)
            e50_series = ta.trend.ema_indicator(df15['c'], 50).fillna(0)
            
            r15_live = r15_series.iloc[-1]
            e20_live = e20_series.iloc[-1]
            e50_live = e50_series.iloc[-1]
            
            p_closed = df15['c'].iloc[-2]
            r15_closed = r15_series.iloc[-2]
            e50_closed = e50_series.iloc[-2]
            
            e200_series = ta.trend.ema_indicator(df1h['c'], 200)
            if len(df1h) >= 200 and e200_series is not None and not pd.isna(e200_series.iloc[-1]) and e200_series.iloc[-1] > 0:
                e200 = e200_series.iloc[-1]
                ema_200_available = True
            else:
                e200 = 0.0
                ema_200_available = False
            
            m_obj = ta.trend.MACD(df1h['c'])
            mv = m_obj.macd().iloc[-1]
            ms = m_obj.macd_signal().iloc[-1]
            
            pats15 = get_advanced_pats(df15)
            
            cur = load_state()
            in_pos = cur.get("in_position", False)
            entry_p = cur.get("entry_price", 0.0)
            
            for k, v in DEFAULT_STATE.items():
                if k not in cur:
                    cur[k] = v
                    
            if in_pos:
                pos_size_usd = cur.get("pos_size", 0.0)
                if position_type == "LONG":
                    l_pnl = ((p / entry_p) - 1) * 100 * LEVERAGE
                    l_val = pos_size_usd * ((p / entry_p) - 1)
                else: 
                    l_pnl = (1 - (p / entry_p)) * 100 * LEVERAGE
                    l_val = pos_size_usd * (1 - (p / entry_p))
            else:
                l_pnl = 0.0
                l_val = 0.0

            time_since_last_trade = time.time() - last_trade_time
            cooldown_over = time_since_last_trade >= COOLDOWN_SECONDS

            pats1h = get_advanced_pats(df1h)
            bull_signal = any(pt['t'] == 'bull' for pt in pats15) or any(pt['t'] == 'bull' for pt in pats1h)
            bear_signal = any(pt['t'] == 'bear' for pt in pats15) or any(pt['t'] == 'bear' for pt in pats1h)
            
            macro_bullish = (p > e200) if ema_200_available else False
            macro_bearish = (p < e200) if ema_200_available else False
            
            ema_long_alignment = p > e20_live and p > e50_live
            ema_short_alignment = p < e20_live and p < e50_live
            
            # ৬৩৪ নম্বর লাইনে SynatxError সংশোধিত অপারেটর: and
            can_buy_long = (macro_bullish and 
                            ema_long_alignment and 
                            btc_bullish and 
                            volume_confirmed and 
                            vwap_long_confirmed and 
                            (40 < r15_live < 65) and 
                            (mv > ms) and 
                            bull_signal and 
                            cooldown_over)

            can_buy_short = (macro_bearish and 
                             ema_short_alignment and 
                             btc_bearish and 
                             volume_confirmed and 
                             vwap_short_confirmed and 
                             (35 < r15_live < 60) and 
                             (mv < ms) and 
                             bear_signal and 
                             cooldown_over)

            long_smart_sell = (p_closed < e50_closed) or (r15_closed > 78)
            short_smart_sell = (p_closed > e50_closed) or (r15_closed < 22)

            # =====================================================================
            # SECTION 4.1: টাইমিং বোতলনেক ক্যালকুলেটর
            # =====================================================================
            now = datetime.now(timezone.utc)
            minutes_to_next_15m = 15 - (now.minute % 15)
            seconds_to_next_15m = (minutes_to_next_15m * 60) - now.second
            
            t_cooldown = max(0.0, COOLDOWN_SECONDS - time_since_last_trade)
            
            if in_pos:
                est_str = "পজিশন সক্রিয়"
            elif not ema_200_available:
                est_str = "পর্যাপ্ত ডাটা নেই (অপেক্ষা করুন)"
            else:
                btc_atr_15m = ta.volatility.average_true_range(high=df_btc['h'], low=df_btc['l'], close=df_btc['c'], window=14).fillna(0).iloc[-1]
                
                t_btc = 0.0
                t_vwap = 0.0
                t_ema = 0.0
                t_macd = 0.0
                
                if p > e200:
                    if btc_p < btc_e20:
                        btc_gap = btc_e20 - btc_p
                        t_btc = (btc_gap / max(0.1, btc_atr_15m)) * 900
                        
                    if p < vwap:
                        vwap_gap = vwap - p
                        t_vwap = (vwap_gap / max(0.01, atr)) * 900
                        
                    if p < e20_live or p < e50_live:
                        ema_gap = max(e20_live, e50_live) - p
                        t_ema = (ema_gap / max(0.01, atr)) * 900
                        
                    if mv < ms:
                        macd_gap = ms - mv
                        t_macd = (macd_gap / 0.05) * 3600
                        
                else:
                    if btc_p > btc_e20:
                        btc_gap = btc_p - btc_e20
                        t_btc = (btc_gap / max(0.1, btc_atr_15m)) * 900
                        
                    if p > vwap:
                        vwap_gap = p - vwap
                        t_vwap = (vwap_gap / max(0.01, atr)) * 900
                        
                    if p > e20_live or p > e50_live:
                        ema_gap = p - min(e20_live, e50_live)
                        t_ema = (ema_gap / max(0.01, atr)) * 900
                        
                    if mv > ms:
                        macd_gap = mv - ms
                        t_macd = (macd_gap / 0.05) * 3600
                
                t_bottleneck = max(t_cooldown, t_btc, t_vwap, t_ema, t_macd)
                
                if t_bottleneck == 0.0:
                    t_final_seconds = seconds_to_next_15m
                else:
                    t_final_seconds = max(seconds_to_next_15m, t_bottleneck)
                    
                est_str = format_seconds_to_bengali(t_final_seconds)

            # =====================================================================
            # SECTION 4.2: পজিশন এক্সিকিউশন ও ট্রেইলিং
            # =====================================================================
            if in_pos:
                initial_sl_dist_pct = cur.get("entry_sl_pct", DEF_SL)
                if initial_sl_dist_pct <= 0:
                    initial_sl_dist_pct = DEF_SL
                
                # LONG পজিশন ম্যানেজমেন্ট
                if position_type == "LONG":
                    breakeven_trigger = entry_p * (1 + (0.6 * initial_sl_dist_pct))
                    if p >= breakeven_trigger and cur["sl_level"] < entry_p:
                        cur.update({"sl_level": round(entry_p, 2)})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🛡️ SL Breakeven-এ উন্নীত [🟢 LONG]"})

                    if p > peak_p:
                        peak_p = p
                        new_sl = round(p * (1 - initial_sl_dist_pct), 2)
                        if new_sl > cur["sl_level"]:
                            cur.update({"sl_level": new_sl, "peak_p": peak_p})

                    if high_p >= cur["tp_level"] or low_p <= cur["sl_level"] or long_smart_sell:
                        in_pos = False
                        position_type = "NONE"
                        
                        if low_p <= cur["sl_level"]:
                            exit_p = cur["sl_level"]
                            exit_reason = "Stop Loss"
                        elif high_p >= cur["tp_level"]:
                            exit_p = cur["tp_level"]
                            exit_reason = "Take Profit"
                        else:
                            exit_p = p
                            exit_reason = "Smart Exit"
                            
                        final_pnl_val = pos_size_usd * ((exit_p / entry_p) - 1)
                        fee_usd = pos_size_usd * 0.0012
                        final_pnl_val = final_pnl_val - fee_usd
                        
                        net_pnl += final_pnl_val
                        if exit_p > entry_p: wins += 1
                        
                        best_val = cur.get("best", 0.0)
                        worst_val = cur.get("worst", 0.0)
                        if final_pnl_val > best_val: best_val = final_pnl_val
                        if final_pnl_val < worst_val: worst_val = final_pnl_val
                        
                        last_trade_time = time.time()
                        cur.update({
                            "balance": round(100.0 + net_pnl, 2),
                            "total_pnl": round(net_pnl, 2),
                            "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
                            "best": round(best_val, 2),
                            "worst": round(worst_val, 2),
                            "last_action": "SELL",
                            "in_position": False,
                            "position_type": "NONE",
                            "pos_size": 0.0,
                            "margin": 0.0,
                            "peak_p": 0.0,
                            "entry_sl_pct": 0.0,
                            "last_trade_time": last_trade_time
                        })
                        cur["history"].insert(0, {
                            "ts": int(time.time()),
                            "t": datetime.now().strftime("%H:%M"), 
                            "a": "SELL", 
                            "p": round(exit_p, 2), 
                            "r": f"{round((final_pnl_val / (pos_size_usd / LEVERAGE)) * 100, 2)}%"
                        })
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 LONG Exit @ ${exit_p:.2f} ({exit_reason}, Fee: ${fee_usd:.2f})"})

                # SHORT পজিশন ম্যানেজমেন্ট
                elif position_type == "SHORT":
                    breakeven_trigger = entry_p * (1 - (0.6 * initial_sl_dist_pct))
                    if p <= breakeven_trigger and cur["sl_level"] > entry_p:
                        cur.update({"sl_level": round(entry_p, 2)})
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": "🛡️ SL Breakeven-এ উন্নীত [🔴 SHORT]"})

                    if valley_p == 0.0 or p < valley_p:
                        valley_p = p
                        new_sl = round(p * (1 + initial_sl_dist_pct), 2)
                        if cur["sl_level"] == 0.0 or new_sl < cur["sl_level"]:
                            cur.update({"sl_level": new_sl, "valley_p": valley_p})

                    if low_p <= cur["tp_level"] or high_p >= cur["sl_level"] or short_smart_sell:
                        in_pos = False
                        position_type = "NONE"
                        
                        if high_p >= cur["sl_level"]:
                            exit_p = cur["sl_level"]
                            exit_reason = "Stop Loss"
                        elif low_p <= cur["tp_level"]:
                            exit_p = cur["tp_level"]
                            exit_reason = "Take Profit"
                        else:
                            exit_p = p
                            exit_reason = "Smart Exit"
                            
                        final_pnl_val = pos_size_usd * (1 - (exit_p / entry_p))
                        fee_usd = pos_size_usd * 0.0012
                        final_pnl_val = final_pnl_val - fee_usd
                        
                        net_pnl += final_pnl_val
                        if exit_p < entry_p: wins += 1
                        
                        best_val = cur.get("best", 0.0)
                        worst_val = cur.get("worst", 0.0)
                        if final_pnl_val > best_val: best_val = final_pnl_val
                        if final_pnl_val < worst_val: worst_val = final_pnl_val
                        
                        last_trade_time = time.time()
                        cur.update({
                            "balance": round(100.0 + net_pnl, 2),
                            "total_pnl": round(net_pnl, 2),
                            "win_rate": round((wins / total) * 100, 1) if total > 0 else 0,
                            "best": round(best_val, 2),
                            "worst": round(worst_val, 2),
                            "last_action": "SELL",
                            "in_position": False,
                            "position_type": "NONE",
                            "pos_size": 0.0,
                            "margin": 0.0,
                            "valley_p": 0.0,
                            "entry_sl_pct": 0.0,
                            "last_trade_time": last_trade_time
                        })
                        cur["history"].insert(0, {
                            "ts": int(time.time()),
                            "t": datetime.now().strftime("%H:%M"), 
                            "a": "SELL", 
                            "p": round(exit_p, 2), 
                            "r": f"{round((final_pnl_val / (pos_size_usd / LEVERAGE)) * 100, 2)}%"
                        })
                        cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SHORT Exit @ ${exit_p:.2f} ({exit_reason}, Fee: ${fee_usd:.2f})"})
            else:
                if can_buy_long:
                    entry_p = p
                    peak_p = p
                    in_pos = True
                    position_type = "LONG"
                    total += 1
                    
                    account_balance = cur.get("balance", INITIAL_FUND)
                    risk_amount = account_balance * RISK_FRACTION
                    pos_size_usd = risk_amount / dynamic_sl_pct
                    pos_size_usd = max(10.0, min(account_balance * LEVERAGE, pos_size_usd)) 
                    margin_usd = pos_size_usd / LEVERAGE  
                    
                    cur.update({
                        "trades": total,
                        "balance": round(account_balance, 2),
                        "in_position": True,
                        "position_type": "LONG",
                        "sl_level": round(p * (1 - dynamic_sl_pct), 2),
                        "tp_level": round(p * (1 + dynamic_tp_pct), 2),
                        "last_action": "BUY",
                        "pos_size": round(pos_size_usd, 2),
                        "margin": round(margin_usd, 2),
                        "peak_p": peak_p,
                        "entry_sl_pct": dynamic_sl_pct 
                    })
                    cur["history"].insert(0, {
                        "ts": int(time.time()),
                        "t": datetime.now().strftime("%H:%M"), 
                        "a": "BUY", 
                        "p": round(p, 2), 
                        "r": "---" 
                    })
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY [LONG] @ ${p:.2f} (Size: ${pos_size_usd:.2f})"})
                
                elif can_buy_short:
                    entry_p = p
                    valley_p = p
                    in_pos = True
                    position_type = "SHORT"
                    total += 1
                    
                    account_balance = cur.get("balance", INITIAL_FUND)
                    risk_amount = account_balance * RISK_FRACTION
                    pos_size_usd = risk_amount / dynamic_sl_pct
                    pos_size_usd = max(10.0, min(account_balance * LEVERAGE, pos_size_usd)) 
                    margin_usd = pos_size_usd / LEVERAGE  
                    
                    cur.update({
                        "trades": total,
                        "balance": round(account_balance, 2),
                        "in_position": True,
                        "position_type": "SHORT",
                        "sl_level": round(p * (1 + dynamic_sl_pct), 2), 
                        "tp_level": round(p * (1 - dynamic_tp_pct), 2), 
                        "last_action": "BUY",
                        "pos_size": round(pos_size_usd, 2),
                        "margin": round(margin_usd, 2),
                        "valley_p": valley_p,
                        "entry_sl_pct": dynamic_sl_pct 
                    })
                    cur["history"].insert(0, {
                        "ts": int(time.time()),
                        "t": datetime.now().strftime("%H:%M"), 
                        "a": "BUY", 
                        "p": round(p, 2), 
                        "r": "---" 
                    })
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY [SHORT] @ ${p:.2f} (Size: ${pos_size_usd:.2f})"})
            
            confluences = {
                "macro_bullish": bool(macro_bullish),
                "btc_bullish": bool(btc_bullish),
                "vwap_long": bool(vwap_long_confirmed),
                "volume_confirmed": bool(volume_confirmed),
                "ema_long": bool(ema_long_alignment),
                "macd_long": bool(mv > ms),
                "bull_signal": bool(bull_signal),
                "macro_bearish": bool(macro_bearish),
                "btc_bearish": bool(btc_bearish),
                "vwap_short": bool(vwap_short_confirmed),
                "ema_short": bool(ema_short_alignment),
                "macd_short": bool(mv < ms),
                "bear_signal": bool(bear_signal)
            }
            
            if position_type == "LONG":
                exit_conditions = {
                    "sl_safe": bool(low_p > cur.get("sl_level", 0.0)),
                    "tp_safe": bool(high_p < cur.get("tp_level", 99999.0)),
                    "ema50_safe": bool(p_closed >= e50_closed),
                    "rsi_safe": bool(r15_closed <= 78),
                    "is_breakeven": bool(cur.get("sl_level", 0.0) >= entry_p)
                }
            elif position_type == "SHORT":
                exit_conditions = {
                    "sl_safe": bool(high_p < cur.get("sl_level", 99999.0)),
                    "tp_safe": bool(low_p > cur.get("tp_level", 0.0)),
                    "ema50_safe": bool(p_closed <= e50_closed),
                    "rsi_safe": bool(r15_closed >= 22),
                    "is_breakeven": bool(cur.get("sl_level", 99999.0) <= entry_p)
                }
            else:
                exit_conditions = {
                    "sl_safe": True, "tp_safe": True, "ema50_safe": True, "rsi_safe": True, "is_breakeven": False
                }

            analysis_15m = {
                "rsi": round(r15_live, 1),
                "ema20": round(e20_live, 2),
                "ema50": round(e50_live, 2),
                "vwap": round(vwap, 2),
                "sig": "বুলিশ ✅" if p > e20_live else "বেয়ারিশ ❌",
                "pats": pats15
            }
            analysis_30m = analyze_tf(df30m, p)
            analysis_45m = analyze_tf(df45m, p)
            
            analysis_1h = analyze_tf(df1h, p)
            analysis_1h.update({
                "ema200": round(e200, 2),
                "btc_price": round(btc_p, 1),
                "sig": "বুলিশ ✅" if p > e200 else "বেয়ারিশ ❌" if ema_200_available else "ডাটা নেই 🛑"
            })
            
            analysis_2h = analyze_tf(df2h, p)
            analysis_3h = analyze_tf(df3h, p)
            analysis_4h = analyze_tf(df4h, p)
            analysis_1d = analyze_tf(df1d, p)

            cur.update({
                "price": round(p, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos,
                "position_type": position_type,
                "live_pnl_pct": round(l_pnl, 2),
                "live_pnl_val": round(l_val, 2),
                "entry_price": round(entry_p, 2),
                "confluences": confluences,
                "exit_conditions": exit_conditions,
                "estimated_time": est_str,
                "pdh": clean_float(pdh),
                "pdl": clean_float(pdl),
                "h4_res": clean_float(h4_res),
                "h4_sup": clean_float(h4_sup),
                
                "analysis_15m": analysis_15m,
                "analysis_30m": analysis_30m,
                "analysis_45m": analysis_45m,
                "analysis_1h": analysis_1h,
                "analysis_2h": analysis_2h,
                "analysis_3h": analysis_3h,
                "analysis_4h": analysis_4h,
                "analysis_1d": analysis_1d
            })
            
            if in_pos:
                cur["wait_reason"] = f"পজিশন সক্রিয় [{position_type}]"
            elif not ema_200_available:
                cur["wait_reason"] = "পর্যাপ্ত ডাটা নেই (EMA 200 লোড হচ্ছে, ট্রেড বন্ধ 🛑)"
            elif not cooldown_over:
                remaining_seconds = int(COOLDOWN_SECONDS - time_since_last_trade)
                cur["wait_reason"] = f"কুলডাউন ({int(remaining_seconds/60)} মিনিট বাকি)"
            elif not btc_bullish and p > e200:
                cur["wait_reason"] = "বিটকয়েন ট্রেন্ড ডাউন (BTC Bearish)"
            elif btc_bullish and p < e200:
                cur["wait_reason"] = "বিটকয়েন ট্রেন্ড আপ (SOL SHORT এর উপযুক্ত নয়)"
            elif not vwap_long_confirmed and p > e200:
                cur["wait_reason"] = "মূল্য VWAP লাইনের নিচে (Bearish Volume Zone)"
            elif not vwap_short_confirmed and p < e200:
                cur["wait_reason"] = "মূল্য VWAP লাইনের ওপরে (Bullish Volume Zone)"
            elif not volume_confirmed:
                cur["wait_reason"] = "দুর্বল ভলিউম (ভলিউম ব্রেকআউটের অপেক্ষা)"
            elif not ema_long_alignment and p > e200:
                cur["wait_reason"] = "১৫-মিনিট চার্টে ল্যাপ বা রিট্রেসমেন্ট চলছে"
            elif not ema_short_alignment and p < e200:
                cur["wait_reason"] = "১৫-মিনিট চার্টে বাউন্স ব্যাক বা কারেকশন চলছে"
            else:
                cur["wait_reason"] = "সুইং এন্ট্রি প্যাটার্ন খুঁজছে..."
                
            save_state(cur)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot Engine Warning: {e}")
            
        time.sleep(10)


# ব্যাকগ্রাউন্ড থ্রেড চালু করা
threading.Thread(target=bot_engine, daemon=True).start()


# =====================================================================
# SECTION 5: Flask ওয়েব সার্ভার এবং এপিআই রাউটস
# =====================================================================
@app.route('/api/data')
def api():
    return jsonify(load_state())


@app.route('/api/ohlcv')
def api_ohlcv():
    global GLOBAL_DF
    try:
        with GLOBAL_DF_LOCK:
            if not GLOBAL_DF.empty:
                df_limit = GLOBAL_DF.tail(200)
                candles = []
                for _, row in df_limit.iterrows():
                    candles.append({
                        "time": int(row['t'] / 1000), 
                        "open": float(row['o']),
                        "high": float(row['h']),
                        "low": float(row['l']),
                        "close": float(row['c'])
                    })
                return jsonify(candles)
    except Exception as e:
        print(f"Error serving ohlcv data for chart: {e}")
    return jsonify([])


@app.route('/')
def index():
    return render_template_string(UI)


# =====================================================================
# SECTION 6: ড্যাশবোর্ড UI টেমপ্লেট
# =====================================================================
UI = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Master SOL Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
    <script>setInterval(() => location.reload(), 600000);</script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { border: 1px solid #dcfce7; color: #166534; padding: 2px 10px; border-radius: 99px; font-size: 10px; font-weight: 800; display: inline-block; margin: 2px; }
        .tag-bull { background: #f0fdf4; } 
        .tag-bear { background: #fef2f2; color: #991b1b; border-color: #fee2e2; }
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
    </style>
</head>
<body class="p-3 text-slate-800">
<div class="max-w-md mx-auto">
    <div class="flex justify-center gap-2 mb-6 text-center">
        <span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span>
        <span class="bg-blue-100 text-blue-700 px-4 py-1 rounded-lg text-xs font-bold border border-blue-200">&#128640; ফিউচারส์ ১০x লিভারেজ</span>
    </div>
    
    <div class="grid grid-cols-3 gap-2 mb-2 text-center text-[10px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>মোট ট্রেড</p><p id="t" class="text-lg font-black text-slate-800">0</p></div>
        <div class="card p-3"><p>জয়ের হার</p><p id="w" class="text-lg font-black text-slate-800">0%</p></div>
        <div class="card p-3"><p>মোট P&L</p><p id="pnl" class="text-lg font-black text-green-600">+$0.00</p></div>
    </div>

    <div class="grid grid-cols-3 gap-2 mb-4 text-center text-[9px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>সেরা</p><p id="bt" class="text-xs font-bold text-green-400">--</p></div>
        <div class="card p-3"><p>খারাপ</p><p id="wt" class="text-xs font-bold text-red-400">--</p></div>
        <div class="card p-3"><p>শেষ</p><p id="la" class="text-xs font-bold text-slate-500">---</p></div>
    </div>

    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4">
            <span id="pr" class="text-4xl font-black tracking-tighter">$0.00</span>
            <div class="text-right text-[10px] text-slate-400 font-bold">ব্যালেন্স: <b id="bl">$100.00</b></div>
        </div>
        
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg">
            <div class="flex justify-between items-center mb-2">
                <p class="text-[10px] font-bold text-slate-400 uppercase">লাইভ পজিশন প্রফিট</p>
                <span id="pos_type" class="text-[10px] font-black px-2 py-0.5 rounded uppercase">NONE</span>
            </div>
            <p id="lp" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t pt-2">
                <div class="text-red-500">🛑 SL: <span id="sl">0</span></div>
                <div class="text-green-600">✅ TP: <span id="tp">0</span></div>
            </div>
        </div>

        <div class="flex justify-between items-center text-[11px] text-slate-400 font-bold mb-3 border-t pt-3">
            <span>আনুমানিক পরবর্তী ট্রেড:</span>
            <span id="est" class="text-slate-800 font-black">লোড হচ্ছে...</span>
        </div>
        
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 text-center uppercase tracking-wide italic">&#8987; লোড হচ্ছে...</div>
    </div>

    <div class="card p-4 mb-4 text-[11px]">
        <h3 class="font-bold text-slate-700 text-xs mb-3 flex items-center justify-between">
            <span>🛡️ ডেইলি ও ৪ ঘণ্টা সাপোর্ট-রেজিস্ট্যান্স জোন</span>
            <span class="text-[9px] px-2 py-0.5 rounded font-black bg-purple-100 text-purple-700 border border-purple-200 uppercase">LOCAL S&R</span>
        </h3>
        <div class="grid grid-cols-2 gap-3 text-slate-500 font-semibold">
            <div class="p-2 bg-slate-50 rounded-lg">Daily High (PDH): <b id="pdh" class="text-red-500 block text-sm mt-1">$0.00</b></div>
            <div class="p-2 bg-slate-50 rounded-lg">Daily Low (PDL): <b id="pdl" class="text-green-600 block text-sm mt-1">$0.00</b></div>
            <div class="p-2 bg-slate-50 rounded-lg">H4 Resistance: <b id="h4_res" class="text-red-500 block text-sm mt-1">$0.00</b></div>
            <div class="p-2 bg-slate-50 rounded-lg">H4 Support: <b id="h4_sup" class="text-green-600 block text-sm mt-1">$0.00</b></div>
        </div>
    </div>

    <div class="card p-4 mb-4 text-[11px]">
        <h3 class="font-bold text-slate-700 text-xs mb-3 flex justify-between items-center">
            <span>🛡️ প্রাতিষ্ঠানিক টু-ওয়ে চেকলিস্ট</span>
            <span class="text-[9px] px-2 py-0.5 rounded font-black bg-blue-100 text-blue-700 border border-blue-200 uppercase">2-WAY MONITOR</span>
        </h3>
        
        <div class="border-b pb-3 mb-3 border-slate-100">
            <p class="text-[10px] font-black text-green-700 mb-2 flex items-center gap-1">🟢 LONG MODE (আপট্রেন্ড কন্ডিশনস) 📈</p>
            <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="long_checklist"></div>
        </div>

        <div>
            <p class="text-[10px] font-black text-red-700 mb-2 flex items-center gap-1">🔴 SHORT MODE (ডাউনট্রেন্ড কন্ডিশনস) 📉</p>
            <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="short_checklist"></div>
        </div>
    </div>
    
    <div class="card p-4 mb-4 text-[11px] hidden" id="exit_checklist_panel">
        <h3 class="font-bold text-slate-700 text-xs mb-3 flex justify-between items-center">
            <span>🚪 এক্সিট কন্ডিশনস চেকলিস্ট</span>
            <span id="exit_mode" class="text-[9px] px-2 py-0.5 rounded font-black uppercase">EXIT MONITOR</span>
        </h3>
        <div class="grid grid-cols-2 gap-2 text-slate-600 font-semibold" id="exit_checklist"></div>
    </div>

    <div class="card p-4 mb-4 text-[11px]">
        <div class="flex justify-between mb-3 items-center border-b pb-2">
            <h3 class="font-bold text-slate-700 text-xs">&#128202; টাইমফ্রেম বিশ্লেষণ</h3>
            <span id="tf_sig" class="font-bold px-2 py-0.5 rounded text-[10px]">WAIT</span>
        </div>
        
        <div class="flex gap-1 overflow-x-auto pb-2 mb-3 scrollbar-hide select-none">
            <button onclick="selectTF('15m')" id="btn_15m" class="px-3 py-1.5 rounded-lg font-black text-[11px] bg-blue-600 text-white shadow-sm border border-blue-600 flex-shrink-0 transition-all duration-150">15m</button>
            <button onclick="selectTF('30m')" id="btn_30m" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">30m</button>
            <button onclick="selectTF('45m')" id="btn_45m" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">45m</button>
            <button onclick="selectTF('1h')" id="btn_1h" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">1h</button>
            <button onclick="selectTF('2h')" id="btn_2h" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">2h</button>
            <button onclick="selectTF('3h')" id="btn_3h" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">3h</button>
            <button onclick="selectTF('4h')" id="btn_4h" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">4h</button>
            <button onclick="selectTF('1d')" id="btn_1d" class="px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150">1d</button>
        </div>

        <div class="grid grid-cols-2 text-slate-500 font-medium gap-y-1 pb-1">
            <span>RSI: <b id="tf_rsi">0</b></span>
            <span>EMA 20: <b id="tf_e20">0</b></span>
            <span>EMA 50: <b id="tf_e50">0</b></span>
            <span id="tf_vwap_row">VWAP: <b id="tf_vwap">0</b></span>
        </div>
        
        <div id="tf_h1_extra" class="hidden grid grid-cols-2 text-slate-500 font-medium gap-y-1 pt-1 border-t border-slate-50">
            <span>EMA 200: <b id="tf_e200">0</b></span>
            <span>BTC Price: <b id="tf_bp">0</b></span>
        </div>
        
        <div id="tf_pats" class="mt-3 flex flex-wrap gap-1"></div>
    </div>

    <div class="card overflow-hidden h-64 mb-4 border border-slate-100 shadow-inner relative">
        <div id="lightweight_chart" class="w-full h-full"></div>
    </div>
    
    <div class="card p-4 mb-4 overflow-hidden">
        <h3 class="font-black text-slate-700 text-[10px] mb-3 uppercase tracking-wider">&#128203; ট্রেড হিস্ট্রি</h3>
        <div class="overflow-x-auto">
            <table class="w-full text-[10px] text-left">
                <thead class="text-slate-400 border-b">
                    <tr>
                        <th class="pb-2">সময়</th>
                        <th class="pb-2 text-center">ধরন</th>
                        <th class="pb-2 text-right">মূল্য</th>
                        <th class="pb-2 text-right">P&L</th>
                    </tr>
                </thead>
                <tbody id="hb" class="divide-y divide-slate-50"></tbody>
            </table>
        </div>
    </div>
    
    <div class="card p-4 mb-6">
        <h3 class="font-bold text-slate-700 text-xs mb-2 uppercase tracking-widest">&#128214; লাইভ লগ</h3>
        <div id="lg" class="space-y-1 text-[10px]"></div>
    </div>
</div>

<script>
    let active_tf = '15m'; 
    let global_data = null;
    
    let chart = null;
    let candleSeries = null;
    let active_price_lines = []; 

    function initChart() {
        const container = document.getElementById('lightweight_chart');
        if (!container) return;

        container.style.width = '100%';
        container.style.height = '100%';

        const width = container.clientWidth || window.innerWidth || 340;

        chart = LightweightCharts.createChart(container, {
            width: width,
            height: 256,
            layout: {
                background: { type: 'solid', color: '#ffffff' },
                textColor: '#64748b',
                fontFamily: 'Segoe UI, sans-serif'
            },
            grid: {
                vertLines: { color: '#f8fafc' },
                horzLines: { color: '#f8fafc' },
            },
            rightPriceScale: {
                borderColor: '#f1f5f9',
            },
            timeScale: {
                borderColor: '#f1f5f9',
                timeVisible: true,
                secondsVisible: false,
            },
        });

        candleSeries = chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
        });

        window.addEventListener('resize', () => {
            chart.resize(container.clientWidth || window.innerWidth || 340, 256);
        });

        loadCandles();

        setTimeout(() => {
            chart.resize(container.clientWidth || window.innerWidth || 340, 256);
            chart.timeScale().fitContent();
        }, 100);
    }

    async function loadCandles() {
        try {
            const r = await fetch('/api/ohlcv');
            const data = await r.json();
            if (data && data.length > 0) {
                candleSeries.setData(data);
                chart.timeScale().fitContent(); 
                if (global_data) {
                    updateMarkersAndLines();
                }
            }
        } catch (e) {
            console.log("Error loading candles:", e);
        }
    }

    function updateMarkersAndLines() {
        if (!global_data || !candleSeries) return;
        const d = global_data;

        active_price_lines.forEach(line => candleSeries.removePriceLine(line));
        active_price_lines = [];

        if (d.pdh > 0) {
            active_price_lines.push(candleSeries.createPriceLine({
                price: d.pdh, color: '#f87171', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'PDH'
            }));
        }
        if (d.pdl > 0) {
            active_price_lines.push(candleSeries.createPriceLine({
                price: d.pdl, color: '#34d399', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'PDL'
            }));
        }
        if (d.h4_res > 0) {
            active_price_lines.push(candleSeries.createPriceLine({
                price: d.h4_res, color: '#ef4444', lineWidth: 1.5, lineStyle: 1, axisLabelVisible: true, title: 'H4 Res'
            }));
        }
        if (d.h4_sup > 0) {
            active_price_lines.push(candleSeries.createPriceLine({
                price: d.h4_sup, color: '#10b981', lineWidth: 1.5, lineStyle: 1, axisLabelVisible: true, title: 'H4 Sup'
            }));
        }

        if (d.in_position) {
            if (d.entry_price > 0) {
                active_price_lines.push(candleSeries.createPriceLine({
                    price: d.entry_price, color: '#3b82f6', lineWidth: 1.5, lineStyle: 0, axisLabelVisible: true, title: 'ENTRY'
                }));
            }
            if (d.sl_level > 0) {
                active_price_lines.push(candleSeries.createPriceLine({
                    price: d.sl_level, color: '#b91c1c', lineWidth: 1.5, lineStyle: 0, axisLabelVisible: true, title: 'SL 🛑'
                }));
            }
            if (d.tp_level > 0) {
                active_price_lines.push(candleSeries.createPriceLine({
                    price: d.tp_level, color: '#047857', lineWidth: 1.5, lineStyle: 0, axisLabelVisible: true, title: 'TP ✅'
                }));
            }
        }

        if (d.history && d.history.length > 0) {
            const markers = [];
            const sortedHistory = [...d.history].sort((a, b) => a.ts - b.ts);
            
            sortedHistory.forEach(h => {
                if (!h.ts) return;
                const candle_ts = Math.floor(h.ts / 900) * 900;
                const is_buy = h.a === 'BUY';
                
                markers.push({
                    time: candle_ts,
                    position: is_buy ? 'belowBar' : 'aboveBar',
                    color: is_buy ? '#3b82f6' : '#f97316',
                    shape: is_buy ? 'arrowUp' : 'arrowDown',
                    text: is_buy ? 'BUY' : 'SELL',
                    size: 1
                });
            });
            candleSeries.setMarkers(markers);
        }
    }

    function selectTF(tf) {
        active_tf = tf;
        renderActiveTF();
    }

    function renderActiveTF() {
        if (!global_data) return;
        const d = global_data;
        
        const tfs = ['15m', '30m', '45m', '1h', '2h', '3h', '4h', '1d'];
        tfs.forEach(t => {
            const btn = document.getElementById('btn_' + t);
            if (!btn) return;
            if (t === active_tf) {
                btn.className = "px-3 py-1.5 rounded-lg font-black text-[11px] bg-blue-600 text-white shadow-sm border border-blue-600 flex-shrink-0 transition-all duration-150";
            } else {
                btn.className = "px-3 py-1.5 rounded-lg font-bold text-[11px] bg-slate-50 text-slate-500 hover:bg-slate-100 border border-slate-200 flex-shrink-0 transition-all duration-150";
            }
        });

        const tfKey = 'analysis_' + active_tf;
        const tfData = d[tfKey];
        if (!tfData) return;

        document.getElementById('tf_rsi').innerText = tfData.rsi;
        document.getElementById('tf_e20').innerText = '$' + tfData.ema20;
        document.getElementById('tf_e50').innerText = '$' + tfData.ema50;

        const vwapRow = document.getElementById('tf_vwap_row');
        if (active_tf === '15m' && tfData.vwap) {
            vwapRow.classList.remove('hidden');
            document.getElementById('tf_vwap').innerText = '$' + tfData.vwap;
        } else {
            vwapRow.classList.add('hidden');
        }

        const h1ExtraRow = document.getElementById('tf_h1_extra');
        if (active_tf === '1h' && tfData.ema200) {
            h1ExtraRow.classList.remove('hidden');
            document.getElementById('tf_e200').innerText = '$' + tfData.ema200;
            document.getElementById('tf_bp').innerText = '$' + tfData.btc_price;
        } else {
            h1ExtraRow.classList.add('hidden');
        }

        const sigBadge = document.getElementById('tf_sig');
        sigBadge.innerText = tfData.sig;
        if (tfData.sig.includes('বুলিশ')) {
            sigBadge.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-green-50 text-green-700 border border-green-200';
        } else if (tfData.sig.includes('বেয়ারিশ')) {
            sigBadge.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-red-50 text-red-700 border border-red-200';
        } else {
            sigBadge.className = 'font-bold px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200';
        }

        const tag = (p) => `<span class="tag ${p.t==='bull'?'tag-bull':'tag-bear'}">${p.n}</span>`;
        const no_pat = '<p class="text-gray-400 italic text-[10px]">কোনো ক্যান্ডেলস্টিক প্যাটার্ন নেই</p>';
        document.getElementById('tf_pats').innerHTML = tfData.pats.length > 0 ? tfData.pats.map(tag).join('') : no_pat;
    }

    function renderCheckItem(label, is_passed) {
        const icon = is_passed ? '✅' : '❌';
        const color = is_passed ? 'text-slate-800' : 'text-slate-400 font-normal';
        return `<div class="flex items-center gap-1.5 ${color}"><span>${icon}</span><span>${label}</span></div>`;
    }

    async function update() {
        try {
            const r = await fetch('/api/data'); 
            const d = await r.json();
            
            if (d.price > 0) {
                global_data = d; 
                
                document.getElementById('pr').innerText = '$' + d.price; 
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                
                document.getElementById('t').innerText = d.trades; 
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = (d.total_pnl >= 0 ? '+$' : '$') + d.total_pnl.toFixed(2);
                document.getElementById('bt').innerText = '$' + d.best.toFixed(2); 
                document.getElementById('wt').innerText = '$' + d.worst.toFixed(2);
                document.getElementById('la').innerText = d.last_action; 
                document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                document.getElementById('est').innerText = d.estimated_time;
                
                document.getElementById('pdh').innerText = '$' + d.pdh.toFixed(2);
                document.getElementById('pdl').innerText = '$' + d.pdl.toFixed(2);
                document.getElementById('h4_res').innerText = '$' + d.h4_res.toFixed(2);
                document.getElementById('h4_sup').innerText = '$' + d.h4_sup.toFixed(2);
                
                const exitPanel = document.getElementById('exit_checklist_panel');
                if (d.in_position) {
                    exitPanel.classList.remove('hidden');
                    const disp = document.getElementById('pnl_display'); 
                    disp.classList.remove('hidden');
                    
                    document.getElementById('lp').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl').innerText = d.sl_level; 
                    document.getElementById('tp').innerText = d.tp_level;
                    
                    const col = d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
                    document.getElementById('lp').className = 'text-4xl font-black ' + col;
                    disp.className = 'mb-4 p-5 border-2 rounded-3xl text-center bg-white shadow-lg ' + (d.live_pnl_pct >= 0 ? 'border-green-100' : 'border-red-100');
                    
                    const p_type = document.getElementById('pos_type');
                    p_type.innerText = d.position_type;
                    
                    const exit_mode = document.getElementById('exit_mode');
                    const exit_container = document.getElementById('exit_checklist');
                    let exit_html = '';
                    
                    if (d.position_type === 'LONG') {
                        p_type.className = 'text-[10px] font-black px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 uppercase';
                        exit_mode.innerText = 'LONG EXIT MONITOR 🟢';
                        exit_mode.className = 'text-[9px] px-2 py-0.5 rounded font-black bg-green-50 text-green-700 border border-green-100';
                        exit_html += renderCheckItem('স্টপ লস সীমা সুরক্ষিত', d.exit_conditions.sl_safe);
                        exit_html += renderCheckItem('টেক প্রফিট লক্ষ্যের নিচে', d.exit_conditions.tp_safe);
                        exit_html += renderCheckItem('১৫মি ইএমএ ৫০ ট্রেন্ড নিরাপদ', d.exit_conditions.ema50_safe);
                        exit_html += renderCheckItem('আরএসআই এক্সট্রিম জোন নিরাপদ', d.exit_conditions.rsi_safe);
                        exit_html += renderCheckItem('আসল পুঁজি ব্রেক-ইভেনে সুরক্ষিত', d.exit_conditions.is_breakeven);
                    } else if (d.position_type === 'SHORT') {
                        p_type.className = 'text-[10px] font-black px-2 py-0.5 rounded bg-red-50 text-red-700 border border-red-200 uppercase';
                        exit_mode.innerText = 'SHORT EXIT MONITOR 🔴';
                        exit_mode.className = 'text-[9px] px-2 py-0.5 rounded font-black bg-red-50 text-red-700 border border-red-100';
                        exit_html += renderCheckItem('স্টপ লস সীমা সুরক্ষিত', d.exit_conditions.sl_safe);
                        exit_html += renderCheckItem('টেক প্রফিট লক্ষ্যের ওপরে', d.exit_conditions.tp_safe);
                        exit_html += renderCheckItem('১৫মি ইএমএ ৫০ ট্রেন্ড নিরাপদ', d.exit_conditions.ema50_safe);
                        exit_html += renderCheckItem('আরএসআই এক্সট্রিম জোন নিরাপদ', d.exit_conditions.rsi_safe);
                        exit_html += renderCheckItem('আসল পুঁজি ব্রেক-ইভেনে সুরক্ষিত', d.exit_conditions.is_breakeven);
                    } else {
                        p_type.className = 'hidden';
                    }
                    exit_container.innerHTML = exit_html;
                } else { 
                    exitPanel.classList.add('hidden');
                    document.getElementById('pnl_display').classList.add('hidden'); 
                }
                
                const long_container = document.getElementById('long_checklist');
                const short_container = document.getElementById('short_checklist');
                const conf = d.confluences;
                
                let long_html = '';
                long_html += renderCheckItem('১ঘণ্টা ম্যাক্রো আপট্রেন্ড', conf.macro_bullish);
                long_html += renderCheckItem('বিটকয়েন ট্রেন্ড আপ', conf.btc_bullish);
                long_html += renderCheckItem('মূল্য VWAP এর ওপরে', conf.vwap_long);
                long_html += renderCheckItem('১৫মি ইএমএ এলাইনমেন্ট', conf.ema_long);
                long_html += renderCheckItem('১ঘণ্টা ম্যাকডি বুলিশ', conf.macd_long);
                long_html += renderCheckItem('ভলিউম ব্রেকআউট কনফার্ম', conf.volume_confirmed);
                long_html += renderCheckItem('সবুজ ক্যান্ডেল প্যাটার্ন', conf.bull_signal);
                long_container.innerHTML = long_html;
                
                let short_html = '';
                short_html += renderCheckItem('১ঘণ্টা ম্যাক্রো ডাউনট্রেন্ড', conf.macro_bearish);
                short_html += renderCheckItem('বিটকয়েন ট্রেন্ড ডাউন', conf.btc_bearish);
                short_html += renderCheckItem('মূল্য VWAP এর নিচে', conf.vwap_short);
                short_html += renderCheckItem('১৫মি ইএমএ ডাউন-এলাইন', conf.ema_short);
                short_html += renderCheckItem('১ঘণ্টা ম্যাকডি বেয়ারিশ', conf.macd_short);
                short_html += renderCheckItem('ভলিউম ব্রেকআউটের কনফার্ম', conf.volume_confirmed);
                short_html += renderCheckItem('লাল ক্যান্ডেল প্যাটার্ন', conf.bear_signal);
                short_container.innerHTML = short_html;
                
                renderActiveTF();
                updateMarkersAndLines();

                document.getElementById('hb').innerHTML = d.history.slice(0,5).map(h => `
                    <tr class="border-b border-slate-50">
                        <td class="py-2 text-slate-400 font-bold">${h.t}</td>
                        <td class="font-black text-center ${h.a=='BUY'?'text-blue-500':'text-orange-500'}">${h.a}</td>
                        <td class="text-right font-black">$${h.p}</td>
                        <td class="text-right font-black ${h.r.includes('-')?'text-red-400':'text-green-500'}">${h.r}</td>
                    </tr>
                `).join('');
                
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `
                    <div class="flex justify-between text-slate-500 pb-1">
                        <span>${l.t}</span>
                        <span>${l.m}</span>
                    </div>
                `).join('');
            }
        } catch (e) {}
    }

    setInterval(loadCandles, 15000); 
    setInterval(update, 5000); 
    
    window.onload = () => {
        initChart();
        update();
    };
</script>
</body>
</html>
"""


# =====================================================================
# SECTION 7: অ্যাপ্লিকেশন এক্সিকিউশন ব্লক
# =====================================================================
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
