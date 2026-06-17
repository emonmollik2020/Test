import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
STOP_LOSS_PCT = 0.015    
TAKE_PROFIT_PCT = 0.025  

exchange = ccxt.bitget()
VIRTUAL_BALANCE = INITIAL_FUND
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0,
    "last_update": "", "in_position": False,
    "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "log": []
}

app = Flask('')

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

# --- ট্রেডিং লজিক ---
def start_bot_logic():
    global VIRTUAL_BALANCE
    in_position, holdings, entry_price = False, 0.0, 0.0
    while True:
        try:
            df = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=50), columns=['t','o','h','l','c','v'])
            price = df['c'].iloc[-1]
            rsi = ta.momentum.rsi(df['c'], window=14).iloc[-1]
            ema20 = ta.trend.ema_indicator(df['c'], window=20).iloc[-1]

            bot_data.update({"price": round(price, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S")})

            if not in_position and price > ema20 and rsi < 65:
                holdings = (VIRTUAL_BALANCE if VIRTUAL_BALANCE > 0 else INITIAL_FUND) / price
                VIRTUAL_BALANCE, entry_price, in_position = 0, price, True
                bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"🟢 BUY @ ${price:.2f}"})
            
            elif in_position:
                if price >= entry_price * (1+TAKE_PROFIT_PCT) or price <= entry_price * (1-STOP_LOSS_PCT):
                    VIRTUAL_BALANCE = holdings * price
                    in_position = False
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M:%S"), "msg": f"🔴 SELL @ ${price:.2f}"})
        except: pass
        time.sleep(20)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Trading Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 30000);</script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-md mx-auto bg-white p-6 rounded-2xl shadow-lg">
        <h1 class="text-xl font-bold text-center text-green-700 mb-4">🤖 SOL/USDT স্কালাপিং বট</h1>
        <div class="grid grid-cols-2 gap-4 mb-6 text-center">
            <div class="bg-gray-50 p-4 rounded-xl"><p class="text-xs text-gray-400">ব্যালেন্স</p><p class="text-xl font-bold">${{ "%.2f"|format(data.balance or 100) }}</p></div>
            <div class="bg-gray-50 p-4 rounded-xl"><p class="text-xs text-gray-400">P&L</p><p class="text-xl font-bold text-{{ 'green' if data.pnl >= 0 else 'red' }}-500">${{ "%.2f"|format(data.pnl) }}</p></div>
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
