import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন (আপনার চাহিদা অনুযায়ী) ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
STOP_LOSS_PCT = 0.025    # ২.৫% নিচে গেলে স্টপ লস
TAKE_PROFIT_PCT = 0.015  # ১.৫% উপরে গেলে টেক প্রফিট
CHECK_INTERVAL = 15      # প্রতি ১৫ সেকেন্ডে বাজার বিশ্লেষণ (দ্রুত ট্রেডের জন্য)

exchange = ccxt.bitget()

VIRTUAL_BALANCE = INITIAL_FUND
bot_data = {
    "balance": INITIAL_FUND, "price": 0, "pnl": 0,
    "last_update": "", "in_position": False,
    "entry_price": 0, "sl_price": 0, "tp_price": 0,
    "trade_history": [],
    "analysis_1m": {"rsi": 0, "signal": "WAIT", "patterns": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "stoch_k": 0, "signal": "WAIT", "patterns": []},
    "combined_signal": "বিশ্লেষণ চলছে...",
    "signal_ok": None,
    "stats": {"total": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "best": None, "last_action": "---"},
    "log": []
}

# ========== ২. ক্যান্ডেলস্টিক প্যাটার্ন ডিটেকশন (ইউনিকোড সহ) ==========
def detect_patterns(df):
    patterns = []
    if len(df) < 3: return patterns
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    full_range = h - l if h != l else 0.0001
    
    # বুলিশ প্যাটার্ন
    if body > 0 and (min(c, o) - l) >= 1.8 * body and (h - max(c, o)) <= 0.4 * body and c >= o:
        patterns.append("Hammer \U0001F528")
    if pc < po and c > o and c >= po and o <= pc:
        patterns.append("Bullish Engulfing \U0001F4C8")
    if (body / full_range) < 0.08:
        patterns.append("Doji \U00002696")
    
    return patterns

def add_log(msg, kind="info"):
    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "kind": kind})
    bot_data["log"] = bot_data["log"][:20]

# ========== ৩. ট্রেডিং লজিক (দ্রুত ট্রেড নেওয়ার জন্য সহজ শর্ত) ==========
def start_bot_logic():
    global VIRTUAL_BALANCE
    in_position, holdings, entry_price = False, 0.0, 0.0
    wins, total, pnl_list = 0, 0, []

    while True:
        try:
            bars_1m = exchange.fetch_ohlcv(SYMBOL, '1m', limit=50)
            bars_3m = exchange.fetch_ohlcv(SYMBOL, '3m', limit=50)
            df1 = pd.DataFrame(bars_1m, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars_3m, columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]

            # ইন্ডিকেটরসমূহ
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            macd = ta.trend.MACD(df3['c'])
            macd_val = macd.macd().iloc[-1]
            macd_sig = macd.macd_signal().iloc[-1]

            # এন্ট্রি সিগন্যাল (সহজ করা হয়েছে): আপট্রেন্ড + RSI < 65 + MACD পজিটিভ ক্রস
            sig_1m_bull = (price > ema20) and (rsi1 < 65)
            sig_3m_bull = (macd_val > macd_sig)
            strong_buy = sig_1m_bull and sig_3m_bull

            # ড্যাশবোর্ড ডাটা আপডেট
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_position,
                "analysis_1m": {"rsi": round(rsi1, 1), "signal": "BULLISH" if sig_1m_bull else "BEARISH", "patterns": detect_patterns(df1)},
                "analysis_3m": {"rsi": round(ta.momentum.rsi(df3['c']).iloc[-1], 1), "macd": round(macd_val, 3), "signal": "BULLISH" if sig_3m_bull else "WAIT", "patterns": detect_patterns(df3)},
                "combined_signal": "BUY SIGNAL DETECTED" if strong_buy else "Searching for entry...",
                "signal_ok": strong_buy
            })

            # BUY Execution
            if not in_position and strong_buy:
                holdings = VIRTUAL_BALANCE / price
                VIRTUAL_BALANCE, entry_price, in_position = 0, price, True
                total += 1
                bot_data["stats"]["total"] = total
                add_log(f"\U0001F7E2 BUY @ ${price:.2f}", "buy")

            # SELL Execution
            elif in_position:
                pnl_pct = (price / entry_price) - 1
                if pnl_pct >= TAKE_PROFIT_PCT or pnl_pct <= -STOP_LOSS_PCT:
                    VIRTUAL_BALANCE = holdings * price
                    in_position = False
                    trade_pnl = VIRTUAL_BALANCE - INITIAL_FUND
                    pnl_list.append(trade_pnl)
                    if pnl_pct > 0: wins += 1
                    bot_data["stats"].update({
                        "wins": wins, "win_rate": round((wins/total)*100, 1),
                        "total_pnl": round(sum(pnl_list), 2), "best": round(max(pnl_list), 2), "last_action": "SELL"
                    })
                    add_log(f"\U0001F534 SELL @ ${price:.2f} ({pnl_pct*100:+.1f}%)", "sell")

        except: pass
        time.sleep(CHECK_INTERVAL)

# ========== ৪. ড্যাশবোর্ড HTML (পাসওয়ার্ড ছাড়া সরাসরি এক্সেস) ==========
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Pro Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>setInterval(() => location.reload(), 20000);</script>
</head>
<body class="bg-gray-50 p-3 font-sans">
<div class="max-w-md mx-auto">
    <div class="text-center mb-4">
        <h1 class="text-xl font-bold text-gray-800">\\U0001F916 SOL/USDT ট্রেডিং বট</h1>
        <span class="bg-green-100 text-green-700 px-3 py-1 rounded-full text-[10px] font-bold mt-1 border border-green-200 inline-block">বট চলছে</span>
    </div>

    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">মোট ট্রেড</p><p class="text-base font-bold">{{ data.stats.total }}</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">জয়ের হার</p><p class="text-base font-bold text-green-600">{{ data.stats.win_rate }}%</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[10px]">মোট P&L</p><p class="text-base font-bold text-green-500">${{ data.stats.total_pnl }}</p></div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100 text-center">
        <div class="flex justify-between items-center mb-2">
            <span class="text-3xl font-black text-gray-900">${{ data.price }}</span>
            <div class="text-right"><p class="text-[10px] text-gray-400">ব্যালেন্স: <b>${{ "%.2f"|format(data.balance or 100) }}</b></p></div>
        </div>
        <div class="bg-orange-50 text-orange-600 p-2 rounded-lg text-[11px] font-bold border border-orange-100">
            \\U0000231B {{ data.combined_signal }}
        </div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">\\U0001F4CA 1 মিনিট বিশ্লেষণ</h3><span class="bg-blue-100 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_1m.signal }}</span></div>
        <p class="text-[11px] text-gray-500 flex justify-between">RSI: <b>{{ data.analysis_1m.rsi }}</b></p>
        <div class="mt-2 text-[10px]">
            {% for p in data.analysis_1m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full mr-1">{{ p }}</span>{% endfor %}
        </div>
    </div>

    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">\\U0001F4CA 3 মিনিট বিশ্লেষণ</h3><span class="bg-blue-100 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">{{ data.analysis_3m.signal }}</span></div>
        <p class="text-[11px] text-gray-500 flex justify-between">RSI: <b>{{ data.analysis_3m.rsi }}</b> | MACD: <b>{{ data.analysis_3m.macd }}</b></p>
        <div class="mt-2 text-[10px]">
            {% for p in data.analysis_3m.patterns %}<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full mr-1">{{ p }}</span>{% endfor %}
        </div>
    </div>

    <div class="bg-white rounded-2xl shadow-sm overflow-hidden h-56 border border-gray-100 mb-3">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100">
        <h3 class="font-bold text-gray-700 text-xs mb-2">\\U0001F4CB লাইভ লগ</h3>
        {% for entry in data.log[:4] %}<p class="text-[10px] py-1 border-b border-gray-50 last:border-0">{{ entry.time }} - {{ entry.msg }}</p>{% endfor %}
    </div>
</div>
</body>
</html>
"""

app = Flask('')
@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML, data=bot_data)

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
