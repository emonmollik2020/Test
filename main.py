import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন (আপনার দেওয়া সেটিংস) ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
TP_PCT = 0.015  # ১.৫% লাভ হলে বিক্রি
SL_PCT = 0.025  # ২.৫% লস হলে বের হওয়া
CHECK_INTERVAL = 15 

exchange = ccxt.bitget()

# ড্যাশবোর্ডের ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0.0, "last_update": "",
    "in_position": False, "entry_price": 0, "sl_price": 0, "tp_price": 0,
    "trade_history": [],
    "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "bb_u": 0, "bb_l": 0, "signal": "WAIT", "patterns": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "stoch_k": 0, "signal": "WAIT", "patterns": []},
    "combined_signal": "বিশ্লেষণ চলছে...",
    "signal_ok": None,
    "stats": {"total": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "best": 0, "last_action": "---"},
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
    full_range = h - l if h != l else 0.0001
    lower_wick = min(c, o) - l
    upper_wick = h - max(c, o)

    if body > 0 and lower_wick >= 1.8 * body and upper_wick <= 0.4 * body and c >= o:
        patterns.append("Hammer \U0001F528")
    if pc < po and c > o and c >= po and o <= pc:
        patterns.append("Bullish Engulfing \U0001F4C8")
    if (body / full_range) < 0.08:
        patterns.append("Doji \U00002696")
    return patterns

# --- ট্রেডিং লজিক ---
def start_bot_logic():
    global VIRTUAL_BALANCE
    in_position, holdings, entry_price = False, 0.0, 0.0
    wins, total, pnl_list = 0, 0, []
    
    while True:
        try:
            df1 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=100), columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '3m', limit=100), columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]
            
            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            ema50 = ta.trend.ema_indicator(df1['c'], window=50).iloc[-1]
            bb = ta.volatility.BollingerBands(df1['c'])
            
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd = ta.trend.MACD(df3['c'])
            stoch = ta.momentum.StochRSIIndicator(df3['c'])

            # বাই সিগন্যাল
            sig_1m_bull = (price > ema20) and (rsi1 < 65)
            sig_3m_bull = (macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]) and (stoch.stochrsi_k().iloc[-1] < 0.55)

            bot_data.update({
                "price": round(price, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {
                    "rsi": round(rsi1,1), "ema20": round(ema20,2), "ema50": round(ema50,2),
                    "bb_u": round(bb.bollinger_hband().iloc[-1], 2), "bb_l": round(bb.bollinger_lband().iloc[-1], 2),
                    "signal": "BULLISH" if sig_1m_bull else "WAIT", "patterns": detect_patterns(df1)
                },
                "analysis_3m": {
                    "rsi": round(rsi3,1), "macd": round(macd.macd().iloc[-1], 3),
                    "stoch_k": round(stoch.stochrsi_k().iloc[-1]*100, 1),
                    "signal": "BULLISH" if macd.macd().iloc[-1] > macd.macd_signal().iloc[-1] else "WAIT",
                    "patterns": detect_patterns(df3)
                }
            })

            strong_buy = sig_1m_bull and sig_3m_bull
            bot_data["combined_signal"] = "WAITING FOR SIGNAL"
            if strong_buy: bot_data["combined_signal"] = "BUY SIGNAL DETECTED!"
            bot_data["signal_ok"] = strong_buy
            bot_data["in_position"] = in_position

            # BUY Execution
            if not in_position and strong_buy:
                holdings = bot_data["balance"] / price
                bot_data["balance"] = 0
                entry_price = price
                in_position = True
                total += 1
                bot_data["stats"].update({"total": total, "last_action": "BUY"})
                bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "\U0001F7E2 BUY @ $" + str(round(price,2))})
            
            # SELL Execution
            elif in_position:
                pnl_pct = (price / entry_price) - 1
                if pnl_pct >= TP_PCT or pnl_pct <= -SL_PCT:
                    bot_data["balance"] = holdings * price
                    in_position = False
                    trade_pnl = bot_data["balance"] - INITIAL_FUND
                    pnl_list.append(trade_pnl)
                    if pnl_pct > 0: wins += 1
                    bot_data["stats"].update({
                        "wins": wins, "win_rate": round((wins/total)*100, 1),
                        "total_pnl": round(trade_pnl, 2), "best": round(max(pnl_list),2), "last_action": "SELL"
                    })
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "\U0001F534 SELL @ $" + str(round(price,2)) + " (" + str(round(pnl_pct*100,1)) + "%)"})

        except: pass
        time.sleep(CHECK_INTERVAL)

@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Pro Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 30000);</script>
</head>
<body class="bg-gray-50 p-3 font-sans">
    <div class="max-w-md mx-auto">
        <div class="text-center mb-4">
            <h1 class="text-xl font-bold text-gray-800">\U0001F916 SOL/USDT ট্রেডিং বট</h1>
            <span class="bg-green-100 text-green-700 px-3 py-1 rounded-full text-[10px] font-bold mt-1 inline-block border border-green-200">বট চলছে</span>
        </div>

        <div class="grid grid-cols-3 gap-2 mb-4 text-center">
            <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">মোট ট্রেড</p><p class="text-base font-bold">{{ data.stats.total }}</p></div>
            <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">জয়ের হার</p><p class="text-base font-bold text-green-600">{{ data.stats.win_rate }}%</p></div>
            <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">মোট লাভ</p><p class="text-base font-bold text-green-500">+${{ data.stats.total_pnl }}</p></div>
        </div>

        <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
            <div class="flex justify-between items-center mb-2">
                <span class="text-3xl font-black text-gray-900">${{ data.price }}</span>
                <div class="text-right"><p class="text-[10px] text-gray-400">ব্যালেন্স: <b>${{ "%.2f"|format(data.balance) }}</b></p></div>
            </div>
            <div class="bg-orange-50 text-orange-600 p-2 rounded-lg text-[10px] font-bold border border-orange-100 text-center">
                \U0000231B {{ data.combined_signal }}
            </div>
        </div>

        <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
            <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">\U0001F4CA 1 মিনিট বিশ্লেষণ</h3><span class="bg-blue-100 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_1m.signal }}</span></div>
            <div class="grid grid-cols-2 gap-y-1 text-[11px] text-gray-500">
                <div class="flex justify-between px-1"><span>RSI:</span> <b>{{ data.analysis_1m.rsi }}</b></div>
                <div class="flex justify-between px-1"><span>EMA20:</span> <b>${{ data.analysis_1m.ema20 }}</b></div>
                <div class="flex justify-between px-1"><span>EMA50:</span> <b>${{ data.analysis_1m.ema50 }}</b></div>
                <div class="flex justify-between px-1"><span>BB Low:</span> <b>${{ data.analysis_1m.bb_l }}</b></div>
            </div>
            <div class="mt-2 text-[10px]">{% if data.analysis_1m.patterns %}{% for p in data.analysis_1m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full mr-1">{{ p }}</span>{% endfor %}{% else %}<i class="text-gray-400">প্যাটার্ন নেই</i>{% endif %}</div>
        </div>

        <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
            <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">\U0001F4CA 3 মিনিট বিশ্লেষণ</h3><span class="bg-blue-100 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_3m.signal }}</span></div>
            <div class="grid grid-cols-2 gap-y-1 text-[11px] text-gray-500">
                <div class="flex justify-between px-1"><span>RSI:</span> <b>{{ data.analysis_3m.rsi }}</b></div>
                <div class="flex justify-between px-1"><span>MACD:</span> <b>{{ data.analysis_3m.macd }}</b></div>
                <div class="flex justify-between px-1"><span>Stoch K:</span> <b>{{ data.analysis_3m.stoch_k }}</b></div>
            </div>
            <div class="mt-2 text-[10px]">{% if data.analysis_3m.patterns %}{% for p in data.analysis_3m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full mr-1">{{ p }}</span>{% endfor %}{% else %}<i class="text-gray-400">প্যাটার্ন নেই</i>{% endif %}</div>
        </div>

        <div class="bg-white rounded-2xl shadow-sm overflow-hidden h-56 border border-gray-100 mb-3">
            <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
        </div>

        <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100">
            <h3 class="font-bold text-gray-700 text-xs mb-2">\U0001F4CB লাইভ লগ</h3>
            {% if data.log %}{% for entry in data.log[:3] %}<p class="text-[10px] py-1 border-b border-gray-50 last:border-0 text-gray-500">{{ entry.time }} - {{ entry.msg }}</p>{% endfor %}{% else %}<p class="text-[10px] text-gray-400 text-center py-4 italic">কোনো ট্রেড হয়নি</p>{% endif %}
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
