import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন (Active Scalper) ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
TP_PCT = 0.015  # ১.৫% লাভ
SL_PCT = 0.025  # ২.৫% লস
CHECK_INTERVAL = 10 # প্রতি ১০ সেকেন্ডে চেক

exchange = ccxt.bitget()

# ডাটা স্টোরেজ
bot_data = {
    "balance": INITIAL_FUND, "price": 0.0, "pnl": 0.0, "last_update": "",
    "in_position": False, "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "signal": "অপেক্ষা", "patterns": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "stoch_k": 0, "signal": "অপেক্ষা", "patterns": []},
    "wait_reason": "বিশ্লেষণ চলছে...",
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
            df1 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=100), columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '3m', limit=100), columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]
            
            # ইন্ডিকেটর ক্যালকুলেশন
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            ema50 = ta.trend.ema_indicator(df1['c'], window=50).iloc[-1]
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd = ta.trend.macd(df3['c']).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).iloc[-1]
            stoch_k = ta.momentum.stochrsi_k(df3['c']).iloc[-1] * 100

            # আপডেট ডাটা (লাইভ ড্যাশবোর্ডের জন্য)
            bot_data.update({
                "price": round(price, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "ema50": round(ema50,2), "signal": "বুলিশ" if price > ema20 else "বেয়ারিশ", "patterns": detect_patterns(df1)},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd,3), "stoch_k": round(stoch_k,1), "signal": "বুলিশ" if macd > macd_s else "অপেক্ষা", "patterns": detect_patterns(df3)},
                "in_position": in_pos
            })

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
                    all_pnl += trade_pnl
                    if pnl_pct > 0: wins += 1
                    bot_data.update({"total_pnl": round(all_pnl, 2), "win_rate": round((wins/total)*100, 1) if total > 0 else 0})
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "SELL @ $" + str(round(price,2))})

        except: pass
        time.sleep(CHECK_INTERVAL)

# --- রিয়েল-টাইম ডাটা এন্ডপয়েন্ট ---
@app.route('/api/data')
def get_data():
    return jsonify(bot_data)

@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SOL/USDT Live Scalper</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        .card { transition: all 0.3s ease; }
        .pattern-tag { background: #dcfce7; color: #15803d; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 600; margin-right: 4px; }
    </style>
</head>
<body class="bg-gray-50 p-3 font-sans text-gray-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-4">
        <h1 class="text-xl font-bold">&#129302; SOL/USDT ট্রেডিং বট</h1>
        <span class="bg-green-100 text-green-700 px-3 py-1 rounded-full text-[10px] font-bold mt-1 border border-green-200 inline-block">&#9989; বট চলছে</span>
    </div>

    <!-- Summary -->
    <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট ট্রেড</p><p id="total_trades" class="text-base font-bold">0</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">জয়ের হার</p><p id="win_rate" class="text-base font-bold text-green-600">0%</p></div>
        <div class="bg-white p-2 rounded-xl shadow-sm border border-gray-100"><p class="text-gray-400 text-[9px]">মোট P&L</p><p id="total_pnl" class="text-base font-bold text-green-600">$0.00</p></div>
    </div>

    <!-- Price Section -->
    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100 text-center">
        <div class="flex justify-between items-center mb-2">
            <span id="price" class="text-3xl font-black text-gray-900">$0.00</span>
            <div class="text-right text-[10px] text-gray-400">ব্যালেন্স: <b id="balance" class="text-gray-800">$100.00</b></div>
        </div>
        <div id="wait_reason" class="bg-orange-50 text-orange-600 p-2 rounded-lg text-[10px] font-bold border border-orange-100">&#8987; লোড হচ্ছে...</div>
    </div>

    <!-- 1m Analysis -->
    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="sig1" class="bg-red-100 text-red-600 px-2 py-0.5 rounded text-[10px] font-bold">WAIT</span></div>
        <div class="grid grid-cols-2 text-[10px] text-gray-500"><p>RSI: <b id="rsi1" class="text-gray-800">0</b></p><p>EMA20: <b id="ema20" class="text-gray-800">0</b></p></div>
        <div id="pats1" class="mt-2 flex flex-wrap gap-1"></div>
    </div>

    <!-- 3m Analysis -->
    <div class="bg-white p-4 rounded-2xl shadow-sm mb-3 border border-gray-100">
        <div class="flex justify-between mb-2 items-center"><h3 class="font-bold text-gray-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="sig3" class="bg-blue-50 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold">WAIT</span></div>
        <div class="grid grid-cols-2 text-[10px] text-gray-500"><p>RSI: <b id="rsi3" class="text-gray-800">0</b></p><p>MACD: <b id="macd3" class="text-gray-800">0</b></p></div>
        <div id="pats3" class="mt-2 flex flex-wrap gap-1"></div>
    </div>

    <!-- Chart -->
    <div class="bg-white rounded-2xl shadow-sm overflow-hidden h-48 border border-gray-100 mb-3">
        <iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <!-- Logs -->
    <div class="bg-white p-3 rounded-2xl shadow-sm border border-gray-100">
        <h3 class="font-bold text-gray-700 text-xs mb-2">&#128203; লাইভ লগ</h3>
        <div id="logs_container" class="space-y-1"></div>
    </div>
</div>

<script>
    async function updateData() {
        try {
            const res = await fetch('/api/data');
            const d = await res.json();
            document.getElementById('price').innerText = '$' + d.price;
            document.getElementById('balance').innerText = '$' + d.balance.toFixed(2);
            document.getElementById('total_trades').innerText = d.total_trades;
            document.getElementById('win_rate').innerText = d.win_rate + '%';
            document.getElementById('total_pnl').innerText = '$' + d.total_pnl.toFixed(2);
            document.getElementById('wait_reason').innerText = d.wait_reason;
            
            document.getElementById('rsi1').innerText = d.analysis_1m.rsi;
            document.getElementById('ema20').innerText = '$' + d.analysis_1m.ema20;
            document.getElementById('sig1').innerText = d.analysis_1m.signal;
            document.getElementById('sig1').className = d.analysis_1m.signal === 'বুলিশ' ? 'bg-green-100 text-green-600 px-2 py-0.5 rounded text-[10px] font-bold' : 'bg-red-100 text-red-600 px-2 py-0.5 rounded text-[10px] font-bold';

            document.getElementById('rsi3').innerText = d.analysis_3m.rsi;
            document.getElementById('macd3').innerText = d.analysis_3m.macd;
            document.getElementById('sig3').innerText = d.analysis_3m.signal;

            // Patterns
            document.getElementById('pats1').innerHTML = d.analysis_1m.patterns.map(p => `<span class="pattern-tag">${p}</span>`).join('');
            document.getElementById('pats3').innerHTML = d.analysis_3m.patterns.map(p => `<span class="pattern-tag">${p}</span>`).join('');

            // Logs
            document.getElementById('logs_container').innerHTML = d.log.slice(0,3).map(l => `<p class="text-[9px] text-gray-500 border-b border-gray-50 pb-1">${l.time} - ${l.msg}</p>`).join('');
        } catch (e) { console.log("Fetch error"); }
    }
    setInterval(updateData, 5000);
    updateData();
</script>
</body></html>
"""

if __name__ == "__main__":
    threading.Thread(target=start_bot_logic, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
