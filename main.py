import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন (সহজ ও দ্রুত ট্রেডের জন্য) ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
TP_PCT = 0.012  # ১.২% লাভ হলে বিক্রি
SL_PCT = 0.010  # ১.০% লস হলে বিক্রি (টাইট স্টপ লস)
CHECK_INTERVAL = 15  # প্রতি ১৫ সেকেন্ডে বাজার চেক

exchange = ccxt.bitget()

# ড্যাশবোর্ডের ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0, "last_update": "",
    "in_position": False, "total_trades": 0, "win_rate": 0,
    "analysis_1m": {"rsi": 0, "ema20": 0, "signal": "অপেক্ষা"},
    "analysis_3m": {"rsi": 0, "macd": 0, "signal": "অপেক্ষা", "pattern": "নেই"},
    "wait_reason": "বিশ্লেষণ চলছে...",
    "log": []
}

app = Flask('')

# --- ট্রেডিং লজিক (সহজ সংস্করণ) ---
def start_bot_logic():
    global bot_data
    in_position, holdings, entry_price = False, 0.0, 0.0
    wins, total = 0, 0
    
    while True:
        try:
            # ডাটা সংগ্রহ
            df1 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=50), columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '3m', limit=50), columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]
            
            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd_obj = ta.trend.MACD(df3['c'])
            macd_val = macd_obj.macd().iloc[-1]
            macd_sig = macd_obj.macd_signal().iloc[-1]

            # সহজ বাই শর্ত (BUY Logic):
            # ১. দাম EMA 20 এর উপরে (আপট্রেন্ড)
            # ২. ১মি RSI ৬৫ এর নিচে (খুব বেশি দামি নয়)
            # ৩. ৩মি MACD পজিটিভ বা সিগন্যাল লাইনের উপরে
            can_buy = (price > ema20) and (rsi1 < 65) and (macd_val > macd_sig)

            # আপডেট ড্যাশবোর্ড ডাটা
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "signal": "বুলিশ ✅" if price > ema20 else "দুর্বল ❌"},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd_val,3), "signal": "বুলিশ ✅" if macd_val > macd_sig else "অপেক্ষা ⏳"}
            })

            # এন্ট্রি নেওয়া (BUY)
            if not in_position and can_buy:
                holdings = bot_data["balance"] / price
                bot_data["balance"] = 0
                entry_price = price
                in_position = True
                total += 1
                bot_data["total_trades"] = total
                bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"🟢 কেনা হয়েছে @ ${price:.2f}"})
            
            # এক্সিট নেওয়া (SELL)
            elif in_position:
                pnl_pct = (price / entry_price) - 1
                if pnl_pct >= TP_PCT or pnl_pct <= -SL_PCT:
                    bot_data["balance"] = holdings * price
                    in_position = False
                    if pnl_pct > 0: wins += 1
                    bot_data["win_rate"] = round((wins/total)*100, 1)
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": f"🔴 বিক্রি @ ${price:.2f} ({pnl_pct*100:+.1f}%)"})

            # ওয়েইট রিজন
            if not in_position:
                if price <= ema20: bot_data["wait_reason"] = "ট্রেন্ড নিচে (Price < EMA20)"
                elif macd_val <= macd_sig: bot_data["wait_reason"] = "৩মি MACD ক্রস নেই"
                else: bot_data["wait_reason"] = "সব ঠিক আছে, এন্ট্রি নিচ্ছে..."

        except: pass
        time.sleep(CHECK_INTERVAL)

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

# ড্যাশবোর্ড ডিজাইন (আপনার প্রিয় প্রফেশনাল লুক)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Active Scalper</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 30000);</script>
</head>
<body class="bg-gray-100 p-4 font-sans">
    <div class="max-w-md mx-auto">
        <div class="text-center mb-6">
            <h1 class="text-2xl font-black text-gray-800">🤖 SOL/USDT স্কালাপিং</h1>
            <span class="bg-green-500 text-white px-3 py-1 rounded-full text-[10px] font-bold mt-2 inline-block">বট চলছে</span>
        </div>

        <div class="grid grid-cols-3 gap-2 mb-4 text-center">
            <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-gray-400 text-[10px]">ট্রেড</p><p class="text-xl font-bold">{{ data.total_trades }}</p></div>
            <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-gray-400 text-[10px]">উইন রেট</p><p class="text-xl font-bold text-green-500">{{ data.win_rate }}%</p></div>
            <div class="bg-white p-3 rounded-2xl shadow-sm"><p class="text-gray-400 text-[10px]">ব্যালেন্স</p><p class="text-lg font-bold">${{ "%.2f"|format(data.balance or 100) }}</p></div>
        </div>

        <div class="bg-white p-6 rounded-3xl shadow-sm mb-4 text-center">
            <div class="flex justify-between items-center mb-4">
                <span class="text-4xl font-black text-gray-900">${{ data.price }}</span>
                <span class="text-[10px] font-bold uppercase tracking-wider p-1 rounded {{ 'bg-blue-100 text-blue-600' if data.in_position else 'bg-green-100 text-green-600' }}">
                    {{ 'ট্রেডে আছে' if data.in_position else 'সিগন্যাল খুঁজছে' }}
                </span>
            </div>
            <div class="bg-orange-50 text-orange-600 p-2 rounded-xl text-xs font-bold border border-orange-100">
                ⌛ {{ data.wait_reason }}
            </div>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4">
            <div class="bg-white p-4 rounded-3xl shadow-sm">
                <h3 class="font-bold text-gray-700 text-xs mb-2">📊 1মি বিশ্লেষণ</h3>
                <div class="text-[11px] space-y-1">
                    <p class="flex justify-between"><span>RSI:</span> <b>{{ data.analysis_1m.rsi }}</b></p>
                    <p class="flex justify-between"><span>EMA20:</span> <b>${{ data.analysis_1m.ema20 }}</b></p>
                    <p class="text-center font-bold mt-2 {{ 'text-green-500' if 'বুলিশ' in data.analysis_1m.signal else 'text-red-500' }}">{{ data.analysis_1m.signal }}</p>
                </div>
            </div>
            <div class="bg-white p-4 rounded-3xl shadow-sm">
                <h3 class="font-bold text-gray-700 text-xs mb-2">📊 3মি বিশ্লেষণ</h3>
                <div class="text-[11px] space-y-1">
                    <p class="flex justify-between"><span>RSI:</span> <b>{{ data.analysis_3m.rsi }}</b></p>
                    <p class="flex justify-between"><span>MACD:</span> <b>{{ data.analysis_3m.macd }}</b></p>
                    <p class="text-center font-bold mt-2 {{ 'text-green-500' if 'বুলিশ' in data.analysis_3m.signal else 'text-red-500' }}">{{ data.analysis_3m.signal }}</p>
                </div>
            </div>
        </div>

        <div class="bg-white p-5 rounded-3xl shadow-sm mb-4">
            <h3 class="font-bold text-gray-700 text-xs mb-3">📋 লাইভ লগ</h3>
            {% for entry in data.log[:4] %}
            <div class="flex justify-between text-[10px] py-1 border-b border-gray-50 last:border-0">
                <span class="text-gray-400">{{ entry.time }}</span>
                <span class="font-bold">{{ entry.msg }}</span>
            </div>
            {% endfor %}
        </div>

        <div class="bg-white rounded-3xl shadow-sm overflow-hidden h-64 border border-gray-100">
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))            <div class="bg-gray-50 p-4 rounded-xl"><p class="text-xs text-gray-400">P&L</p><p class="text-xl font-bold text-{{ 'green' if data.pnl >= 0 else 'red' }}-500">${{ "%.2f"|format(data.pnl) }}</p></div>
        </div>
        <div class="text-center mb-6 text-4xl font-bold text-gray-800">${{ data.price }}</div>
        <div class="bg-gray-50 p-4 rounded-xl mb-6 shadow-inner">
            <p class="text-sm font-bold border-b pb-2 mb-2 text-gray-700">📋 লাইভ লগ</p>
            {% for entry in data.log[:5] %}<p class="text-xs py-1 border-b last:border-0">{{ entry.time }} - {{ entry.msg }}</p>{% endfor %}
        </div>
        <div class="h-64 rounded-xl overflow-hidden shadow-inner border border-gray-100">
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
