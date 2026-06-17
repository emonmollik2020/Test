import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== 1. কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
TP_PCT = 0.015  # 1.5% লাভ
SL_PCT = 0.025  # 2.5% লস
CHECK_INTERVAL = 15 

exchange = ccxt.bitget()

# ড্যাশবোর্ডের ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0.0, "pnl": 0.0, "last_update": "",
    "in_position": False, "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "bb_l": 0, "signal": "অপেক্ষা", "patterns": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "stoch_k": 0, "signal": "অপেক্ষা", "patterns": []},
    "wait_reason": "বিশ্লেষণ শুরু হচ্ছে...",
    "log": []
}

app = Flask('')

# --- ক্যান্ডেলস্টিক প্যাটার্ন ডিটেকশন ---
def detect_patterns(df):
    patterns = []
    if len(df) < 3: return patterns
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    if body > 0 and (min(c, o) - l) >= 1.8 * body: patterns.append("হ্যামার &#128296;")
    if pc < po and c > o and c >= po: patterns.append("বুলিশ এনগালফিং &#128200;")
    if abs(c - o) <= ((h - l) * 0.1): patterns.append("ডোজি &#9878;")
    return patterns

# --- ট্রেডিং লজিক ---
def start_bot_logic():
    global bot_data
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, all_pnl = 0, 0, 0.0
    
    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]
            
            # ইন্ডিকেটর
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            ema50 = ta.trend.ema_indicator(df1['c'], window=50).iloc[-1]
            bb_l = ta.volatility.bollinger_lband(df1['c']).iloc[-1]
            
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd = ta.trend.macd(df3['c']).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).iloc[-1]
            stoch_k = ta.momentum.stochrsi_k(df3['c']).iloc[-1] * 100

            # আপডেট ডাটা
            bot_data.update({
                "price": round(price, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "ema50": round(ema50,2), "bb_l": round(bb_l,2), "signal": "বুলিশ" if price > ema20 else "বেয়ারিশ", "patterns": detect_patterns(df1)},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd,3), "stoch_k": round(stoch_k,1), "signal": "বুলিশ" if macd > macd_s else "অপেক্ষা", "patterns": detect_patterns(df3)}
            })

            # সিগন্যাল চেক
            if not in_pos:
                if price > ema20 and rsi1 < 65 and macd > macd_s:
                    holdings = bot_data["balance"] / price
                    bot_data["balance"], entry_p, in_pos = 0, price, True
                    total += 1
                    bot_data["total_trades"] = total
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "BUY @ $" + str(round(price,2))})
                
                bot_data["wait_reason"] = "ট্রেন্ড নিচে" if price <= ema20 else "৩মি MACD অপেক্ষা" if macd <= macd_s else "এন্ট্রি খুঁজছে..."

            elif in_pos:
                pnl_pct = (price / entry_p) - 1
                if pnl_pct >= TP_PCT or pnl_pct <= -SL_PCT:
                    bot_data["balance"] = holdings * price
                    in_pos = False
                    trade_pnl = bot_data["balance"] - INITIAL_FUND
                    all_pnl = trade_pnl
                    if pnl_pct > 0: wins += 1
                    bot_data.update({"total_pnl": round(all_pnl, 2), "win_rate": round((wins/total)*100, 1)})
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "SELL @ $" + str(round(price,2))})

        except Exception as e: print(f"Error: {e}")
        time.sleep(15)

@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Pro Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 30000);</script>
</head>
<body class="bg-gray-50 p-3 font-sans text-gray-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-4">
        <h1 class="text-xl font-bold">&#129112; SOL/USDT ট্রেডিং বট</h1>
        <span class="bg-green-100 text-green-700 px-3 py-1 rounded-full text-[10px] font-bold mt-1 border border-green-200 inline-block">&#9989; বট চলছে</span>
    </div>

    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট ট্রেড</p><p class="text-base font-bold">{{ data.total_trades }}</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">জয়ের হার</p><p class="text-base font-bold text-green-600">{{ data.win_rate }}%</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট P&L</p><p class="text-base font-bold text-green-600">${{ data.total_pnl }}</p></div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100 text-center">
        <div class="flex justify-between items-center mb-2"><span class="text-3xl font-black text-gray-900">${{ data.price }}</span><div class="text-right text-[10px] text-gray-400">ব্যালেন্স: <b>${{ "%.2f"|format(data.balance or 100) }}</b></div></div>
        <div class="bg-orange-50 text-orange-600 p-2 rounded-lg text-[10px] font-bold border border-orange-100">&#8987; {{ data.wait_reason }}</div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span class="{% if data.analysis_1m.signal == 'বুলিশ' %}bg-green-100 text-green-600{% else %}bg-red-100 text-red-600{% endif %} px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_1m.signal }}</span></div>
        <div class="grid grid-cols-2 text-[10px] text-gray-500"><p>RSI: <b>{{ data.analysis_1m.rsi }}</b></p><p>EMA20: <b>${{ data.analysis_1m.ema20 }}</b></p></div>
        <div class="mt-2 flex flex-wrap gap-1">{% for p in data.analysis_1m.patterns %}<span class="bg-green-50 text-green-700 px-2 py-0.5 rounded-full text-[9px] font-medium border border-green-100">{{ p|safe }}</span>{% endfor %}</div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span class="bg-blue-50 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_3m.signal }}</span></div>
        <div class="grid grid-cols-2 text-[10px] text-gray-500"><p>RSI: <b>{{ data.analysis_3m.rsi }}</b></p><p>MACD: <b>{{ data.analysis_3m.macd }}</b></p></div>
        <div class="mt-2 flex flex-wrap gap-1">{% for p in data.analysis_3m.patterns %}<span class="bg-green-50 text-green-700 px-2 py-0.5 rounded-full text-[9px] font-medium border border-green-100">{{ p|safe }}</span>{% endfor %}</div>
    </div>

    <div class="bg-white rounded-2xl shadow-sm overflow-hidden h-48 border border-gray-100 mb-3"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>

    <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100">
        <h3 class="font-bold text-gray-700 text-xs mb-2">&#128203; লাইভ লগ</h3>
        {% if data.log %}{% for entry in data.log[:3] %}<p class="text-[9px] py-1 border-b border-gray-50 last:border-0">{{ entry.time }} - {{ entry.msg }}</p>{% endfor %}{% else %}<p class="text-center text-gray-400 text-[10px]">অপেক্ষা করুন...</p>{% endif %}
    </div>
</div>
</body></html>
"""

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
