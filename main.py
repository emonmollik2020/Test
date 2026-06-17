import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
STOP_LOSS_PCT = 0.012    # 1.2% নিচে গেলে কাটা
TAKE_PROFIT_PCT = 0.020  # 2.0% উপরে গেলে লাভ নেওয়া

exchange = ccxt.bitget()

VIRTUAL_BALANCE = INITIAL_FUND
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0,
    "last_update": "", "in_position": False,
    "entry_price": 0, "sl_price": 0, "tp_price": 0,
    "trade_history": [],
    "analysis_1m": {
        "rsi": 0, "ema20": 0, "ema50": 0,
        "bb_upper": 0, "bb_lower": 0,
        "patterns": [], "signal": "অপেক্ষা"
    },
    "analysis_3m": {
        "rsi": 0, "macd": 0, "macd_signal": 0,
        "stoch_k": 0, "stoch_d": 0,
        "patterns": [], "signal": "অপেক্ষা"
    },
    "combined_signal": "ডেটা লোড হচ্ছে...",
    "signal_ok": None,
    "stats": {
        "total": 0, "wins": 0, "win_rate": 0.0,
        "total_pnl": 0.0, "best": None, "worst": None,
        "last_action": "---"
    },
    "log": []
}

# ========== ক্যান্ডেলস্টিক প্যাটার্ন ডিটেকশন (ইমোজি কোড সহ) ==========
def detect_patterns(df):
    patterns = []
    if len(df) < 3:
        return patterns

    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    full_range = h - l if h != l else 0.0001

    # বুলিশ প্যাটার্ন
    if body > 0 and (min(c, o) - l) >= 1.8 * body and (h - max(c, o)) <= 0.4 * body and c >= o:
        patterns.append("হ্যামার &#128296;")

    if pc < po and c > o and o <= pc and c >= po:
        patterns.append("বুলিশ এনগালফিং &#128200;")

    if (body / full_range) < 0.08:
        patterns.append("ডোজি &#9878;")

    if c > o and body / full_range > 0.75 and (h - max(c, o)) < 0.1 * body:
        patterns.append("বুলিশ মারুবোজু &#128170;")

    return patterns

# ========== ইন্ডিকেটর হেলপার ==========
def stoch_rsi(series):
    rsi = ta.momentum.rsi(series, window=14)
    min_rsi = rsi.rolling(14).min()
    max_rsi = rsi.rolling(14).max()
    stoch = 100 * (rsi - min_rsi) / (max_rsi - min_rsi + 1e-9)
    k = stoch.rolling(3).mean()
    d = k.rolling(3).mean()
    return k, d

def add_log(msg, kind="info"):
    bot_data["log"].insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "msg": msg,
        "kind": kind
    })
    bot_data["log"] = bot_data["log"][:20]

# ========== স্ট্যাটস হিসাব ==========
def recalc_stats():
    sells = [t for t in bot_data["trade_history"] if t["type"] == "SELL" and t["pnl"] is not None]
    total = len(sells)
    wins  = sum(1 for t in sells if t["pnl"] > 0)
    pnl_vals = [t["pnl"] for t in sells]
    bot_data["stats"].update({
        "total": total,
        "wins": wins,
        "win_rate": round(wins / total * 100, 1) if total else 0.0,
        "total_pnl": round(sum(pnl_vals), 4) if pnl_vals else 0.0,
        "best": round(max(pnl_vals), 4) if pnl_vals else None,
        "worst": round(min(pnl_vals), 4) if pnl_vals else None,
        "last_action": bot_data["trade_history"][-1]["type"] if bot_data["trade_history"] else "---"
    })

# ========== বট লজিক (Active Scalping) ==========
def start_bot_logic():
    global VIRTUAL_BALANCE
    in_position, holdings, entry_price = False, 0.0, 0.0

    while True:
        try:
            bars_1m = exchange.fetch_ohlcv(SYMBOL, '1m', limit=100)
            bars_3m = exchange.fetch_ohlcv(SYMBOL, '3m', limit=100)
            df1 = pd.DataFrame(bars_1m, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars_3m, columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]

            # 1মি বিশ্লেষণ
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            sig_1m_bull = (price > ema20) and (rsi1 < 65)

            # 3মি বিশ্লেষণ
            macd_obj = ta.trend.MACD(df3['c'])
            macd_val, macd_sig = macd_obj.macd().iloc[-1], macd_obj.macd_signal().iloc[-1]
            sk, sd = stoch_rsi(df3['c'])
            sig_3m_bull = (macd_val > macd_sig) and (sk.iloc[-1] < 55)

            # আপডেট ড্যাশবোর্ড ডাটা
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "signal": "বুলিশ" if sig_1m_bull else "অপেক্ষা", "patterns": detect_patterns(df1)},
                "analysis_3m": {"rsi": round(ta.momentum.rsi(df3['c']).iloc[-1],1), "macd": round(macd_val,3), "stoch_k": round(sk.iloc[-1],1), "signal": "বুলিশ" if sig_3m_bull else "অপেক্ষা", "patterns": detect_patterns(df3)},
                "combined_signal": "শক্তিশালী BUY সিগন্যাল!" if (sig_1m_bull and sig_3m_bull) else "সুযোগ খুঁজছে...",
                "signal_ok": (sig_1m_bull and sig_3m_bull)
            })

            # BUY
            if not in_position and (sig_1m_bull and sig_3m_bull):
                holdings = VIRTUAL_BALANCE / price
                VIRTUAL_BALANCE, entry_price, in_position = 0, price, True
                bot_data["trade_history"].append({"time": datetime.now().strftime("%H:%M"), "type": "BUY", "price": price, "pnl": None})
                add_log(f"BUY @ ${price:.2f}", "buy")

            # SELL
            elif in_position:
                pnl_pct = (price / entry_price) - 1
                if pnl_pct >= TAKE_PROFIT_PCT or pnl_pct <= -STOP_LOSS_PCT:
                    VIRTUAL_BALANCE = holdings * price
                    in_position = False
                    trade_pnl = VIRTUAL_BALANCE - INITIAL_FUND
                    bot_data["trade_history"].append({"time": datetime.now().strftime("%H:%M"), "type": "SELL", "price": price, "pnl": round(trade_pnl, 2)})
                    add_log(f"SELL @ ${price:.2f} ({pnl_pct*100:+.1f}%)", "sell")
                    recalc_stats()

        except: pass
        time.sleep(15)

# ========== HTML ড্যাশবোর্ড ==========
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Trading Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 30000);</script>
</head>
<body class="bg-gray-50 min-h-screen p-3 font-sans text-gray-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-4">
        <h1 class="text-xl font-bold">&#129302; SOL/USDT ট্রেডিং বট</h1>
        <span class="bg-green-100 text-green-700 px-3 py-1 rounded-full text-[10px] font-bold mt-1 border border-green-200 inline-block">&#9989; বট চলছে</span>
    </div>

    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট ট্রেড</p><p class="text-base font-bold">{{ data.stats.total }}</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">জয়ের হার</p><p class="text-base font-bold text-green-600">{{ data.stats.win_rate }}%</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট P&L</p><p class="text-base font-bold text-green-600">${{ data.stats.total_pnl }}</p></div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100 text-center">
        <div class="flex justify-between items-center mb-2">
            <span class="text-3xl font-black text-gray-900">${{ data.price }}</span>
            <div class="text-right text-[10px] text-gray-400">ব্যালেন্স: <b>${{ "%.2f"|format(data.balance or 100) }}</b></div>
        </div>
        <div class="bg-orange-50 text-orange-600 p-2 rounded-lg text-[10px] font-bold border border-orange-100">&#8987; {{ data.combined_signal }}</div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span class="{% if data.analysis_1m.signal == 'বুলিশ' %}bg-green-100 text-green-600{% else %}bg-red-100 text-red-600{% endif %} px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_1m.signal }}</span></div>
        <div class="text-[10px] text-gray-500 flex justify-between"><span>RSI: <b>{{ data.analysis_1m.rsi }}</b></span><span>EMA20: <b>${{ data.analysis_1m.ema20 }}</b></span></div>
        <div class="mt-2 flex flex-wrap gap-1">{% for p in data.analysis_1m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-[9px] font-medium">{{ p|safe }}</span>{% endfor %}</div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span class="{% if data.analysis_3m.signal == 'বুলিশ' %}bg-blue-100 text-blue-600{% else %}bg-gray-100 text-gray-400{% endif %} px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_3m.signal }}</span></div>
        <div class="text-[10px] text-gray-500 flex justify-between"><span>RSI: <b>{{ data.analysis_3m.rsi }}</b></span><span>MACD: <b>{{ data.analysis_3m.macd }}</b></span><span>Stoch K: <b>{{ data.analysis_3m.stoch_k }}</b></span></div>
        <div class="mt-2 flex flex-wrap gap-1">{% for p in data.analysis_3m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-[9px] font-medium">{{ p|safe }}</span>{% endfor %}</div>
    </div>

    <div class="bg-white rounded-2xl shadow-sm overflow-hidden h-48 border border-gray-100 mb-3"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>

    <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100">
        <h3 class="font-bold text-gray-700 text-xs mb-2">&#128203; লাইভ লগ</h3>
        {% if data.log %}{% for entry in data.log[:3] %}<p class="text-[9px] py-1 border-b border-gray-50 last:border-0">{{ entry.time }} - {{ entry.msg }}</p>{% endfor %}{% else %}<p class="text-center text-gray-400 text-[10px]">অপেক্ষা করুন...</p>{% endif %}
    </div>
</div>
</body></html>
"""

app = Flask('')
@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
