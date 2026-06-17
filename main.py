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
TP_PCT = 0.012  # ১.২% লাভ
SL_PCT = 0.010  # ১.০% লস
CHECK_INTERVAL = 15 

exchange = ccxt.bitget()

# ড্যাশবোর্ডের ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0.0, "last_update": "",
    "in_position": False, "total_trades": 0, "win_rate": 0,
    "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "bb_u": 0, "bb_l": 0, "signal": "WAIT"},
    "analysis_3m": {"rsi": 0, "macd": 0, "stoch_k": 0, "signal": "WAIT", "pattern": "None"},
    "wait_reason": "বিশ্লেষণ চলছে...",
    "log": []
}

app = Flask('')

# --- ট্রেডিং লজিক ---
def start_bot_logic():
    global bot_data
    in_position, holdings, entry_price = False, 0.0, 0.0
    wins, total = 0, 0
    
    while True:
        try:
            df1 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=50), columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '3m', limit=50), columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]
            
            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            ema50 = ta.trend.ema_indicator(df1['c'], window=50).iloc[-1]
            bb = ta.volatility.BollingerBands(df1['c'])
            
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd_obj = ta.trend.MACD(df3['c'])
            stoch = ta.momentum.StochRSIIndicator(df3['c'])

            can_buy = (price > ema20) and (rsi1 < 65) and (macd_obj.macd().iloc[-1] > macd_obj.macd_signal().iloc[-1])

            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {
                    "rsi": round(rsi1,1), "ema20": round(ema20,2), "ema50": round(ema50,2),
                    "bb_u": round(bb.bollinger_hband().iloc[-1], 2), "bb_l": round(bb.bollinger_lband().iloc[-1], 2),
                    "signal": "BULLISH" if price > ema20 else "BEARISH"
                },
                "analysis_3m": {
                    "rsi": round(rsi3,1), "macd": round(macd_obj.macd().iloc[-1], 3),
                    "stoch_k": round(stoch.stochrsi_k().iloc[-1] * 100, 1),
                    "signal": "BULLISH" if macd_obj.macd().iloc[-1] > macd_obj.macd_signal().iloc[-1] else "WAIT"
                }
            })

            if not in_position and can_buy:
                holdings = bot_data["balance"] / price
                bot_data["balance"] = 0
                entry_price = price
                in_position = True
                total += 1
                bot_data["total_trades"] = total
                bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "\U0001F7E2 BUY @ $" + str(round(price,2))})
            
            elif in_position:
                pnl_pct = (price / entry_price) - 1
                if pnl_pct >= TP_PCT or pnl_pct <= -SL_PCT:
                    bot_data["balance"] = holdings * price
                    in_position = False
                    if pnl_pct > 0: wins += 1
                    bot_data["win_rate"] = round((wins/total)*100, 1)
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "\U0001F534 SELL @ $" + str(round(price,2))})

            if not in_position:
                if price <= ema20: bot_data["wait_reason"] = "Trend Down (Price < EMA20)"
                else: bot_data["wait_reason"] = "Waiting for MACD/RSI Signal"

        except: pass
        time.sleep(15)

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Trading Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => { location.reload(); }, 30000);</script>
</head>
<body class="bg-gray-50 p-4 font-sans text-gray-800">
    <div class="max-w-md mx-auto">
        <div class="text-center mb-6">
            <h1 class="text-2xl font-black text-gray-800">\U0001F916 SOL/USDT Trading</h1>
            <span class="bg-green-500 text-white px-3 py-1 rounded-full text-[10px] font-bold mt-2 inline-block">BOT LIVE</span>
        </div>

        <div class="grid grid-cols-3 gap-2 mb-4 text-center">
            <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">Trades</p><p class="text-lg font-bold">{{ data.total_trades }}</p></div>
            <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">Win Rate</p><p class="text-lg font-bold text-green-500">{{ data.win_rate }}%</p></div>
            <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">Balance</p><p class="text-lg font-bold">${{ "%.2f"|format(data.balance) }}</p></div>
        </div>

        <div class="bg-white p-6 rounded-3xl shadow-sm mb-4 text-center border border-gray-100">
            <div class="flex justify-between items-center mb-4">
                <span class="text-4xl font-black text-gray-900">${{ data.price }}</span>
                <span class="text-[10px] font-bold p-1 rounded {{ 'bg-blue-100 text-blue-600' if data.in_position else 'bg-green-100 text-green-600' }}">
                    {{ 'TRADING' if data.in_position else 'SEARCHING' }}
                </span>
            </div>
            <div class="bg-orange-50 text-orange-600 p-2 rounded-xl text-xs font-bold border border-orange-100">
                \U0000231B {{ data.wait_reason }}
            </div>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4">
            <div class="bg-white p-4 rounded-3xl shadow-sm border border-gray-100 text-[11px]">
                <h3 class="font-bold text-gray-700 text-xs mb-2">\U0001F4CA 1m Analysis</h3>
                <p class="flex justify-between"><span>RSI:</span> <b>{{ data.analysis_1m.rsi }}</b></p>
                <p class="flex justify-between"><span>EMA20:</span> <b>${{ data.analysis_1m.ema20 }}</b></p>
                <p class="text-center font-bold mt-2 {{ 'text-green-500' if data.analysis_1m.signal == 'BULLISH' else 'text-red-500' }}">{{ data.analysis_1m.signal }}</p>
            </div>
            <div class="bg-white p-4 rounded-3xl shadow-sm border border-gray-100 text-[11px]">
                <h3 class="font-bold text-gray-700 text-xs mb-2">\U0001F4CA 3m Analysis</h3>
                <p class="flex justify-between"><span>RSI:</span> <b>{{ data.analysis_3m.rsi }}</b></p>
                <p class="flex justify-between"><span>MACD:</span> <b>{{ data.analysis_3m.macd }}</b></p>
                <p class="text-center font-bold mt-2 {{ 'text-green-500' if data.analysis_3m.signal == 'BULLISH' else 'text-red-500' }}">{{ data.analysis_3m.signal }}</p>
            </div>
        </div>

        <div class="bg-white p-5 rounded-3xl shadow-sm mb-4 border border-gray-100">
            <h3 class="font-bold text-gray-700 text-xs mb-3">\U0001F4CB Live Logs</h3>
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
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
