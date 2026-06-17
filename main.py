import ccxt
import pandas as pd
import ta
import time
import threading
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== 1. সেটিংস ==========
SYMBOL = "SOL/USDT"
INITIAL_FUND = 100.0
exchange = ccxt.bitget()

# ড্যাশবোর্ড ডাটা (Default Values)
bot_data = {
    "balance": INITIAL_FUND, "price": 0.0, "pnl": 0.0, "last_update": "Loading...",
    "in_position": False, "total_trades": 0, "win_rate": 0, "total_pnl": 0.0,
    "analysis_1m": {"rsi": 0, "ema20": 0, "signal": "অপেক্ষা", "patterns": []},
    "analysis_3m": {"rsi": 0, "macd": 0, "signal": "অপেক্ষা", "patterns": []},
    "wait_reason": "বিশ্লেষণ শুরু হচ্ছে...", "log": []
}

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন ---
def get_patterns(df):
    pats = []
    if len(df) < 3: return pats
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    if body > 0 and (min(c, o) - l) >= 1.8 * body: pats.append("হ্যামার &#128296;")
    if pc < po and c > o and c >= po: pats.append("বুলিশ এনগালফিং &#128200;")
    if abs(c - o) <= ((h - l) * 0.1): pats.append("ডোজি &#9878;")
    return pats

# --- ট্রেডিং লজিক থ্রেড ---
def bot_loop():
    global bot_data
    in_pos, holdings, entry_p = False, 0.0, 0.0
    wins, total, net_pnl = 0, 0, 0.0

    while True:
        try:
            # ডাটা সংগ্রহ
            df1 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '1m', limit=50), columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(exchange.fetch_ohlcv(SYMBOL, '3m', limit=50), columns=['t','o','h','l','c','v'])
            price = df1['c'].iloc[-1]

            # ইন্ডিকেটর
            rsi1 = ta.momentum.rsi(df1['c']).iloc[-1]
            ema20 = ta.trend.ema_indicator(df1['c'], window=20).iloc[-1]
            rsi3 = ta.momentum.rsi(df3['c']).iloc[-1]
            macd = ta.trend.macd(df3['c']).iloc[-1]
            macd_s = ta.trend.macd_signal(df3['c']).iloc[-1]

            # ডাটা আপডেট
            bot_data.update({
                "price": round(price, 2),
                "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "analysis_1m": {"rsi": round(rsi1,1), "ema20": round(ema20,2), "signal": "বুলিশ" if price > ema20 else "বেয়ারিশ", "patterns": get_patterns(df1)},
                "analysis_3m": {"rsi": round(rsi3,1), "macd": round(macd,3), "signal": "বুলিশ" if macd > macd_s else "অপেক্ষা", "patterns": get_patterns(df3)},
                "in_position": in_pos
            })

            # বাই/সেল লজিক
            if not in_pos:
                if price > ema20 and rsi1 < 65 and macd > macd_s:
                    holdings = bot_data["balance"] / price
                    bot_data["balance"], entry_p, in_pos = 0, price, True
                    total += 1
                    bot_data["total_trades"] = total
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "BUY @ $" + str(round(price,2))})
                bot_data["wait_reason"] = "ট্রেন্ড নিচে" if price <= ema20 else "সিগন্যাল খুঁজছে..."
            
            elif in_pos:
                pnl_pct = (price / entry_p) - 1
                if pnl_pct >= 0.015 or pnl_pct <= -0.025:
                    bot_data["balance"] = holdings * price
                    in_pos = False
                    trade_pnl = bot_data["balance"] - INITIAL_FUND
                    if pnl_pct > 0: wins += 1
                    bot_data.update({"total_pnl": round(trade_pnl, 2), "win_rate": round((wins/total)*100, 1) if total > 0 else 0})
                    bot_data["log"].insert(0, {"time": datetime.now().strftime("%H:%M"), "msg": "SELL @ $" + str(round(price,2))})

        except: pass
        time.sleep(10)

# --- রিয়েল-টাইম API ---
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
    <title>SOL Live Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        .card { background: white; border-radius: 16px; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { background: #ecfdf5; color: #15803d; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 700; border: 1px solid #dcfce7; }
    </style>
</head>
<body class="bg-[#f8fafc] p-3 font-sans text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-5"><h1 class="text-xl font-black text-slate-700">&#129302; SOL/USDT ট্রেডিং বট</h1><span class="bg-green-500 text-white px-3 py-0.5 rounded-full text-[10px] font-bold mt-1 inline-block">বট চলছে</span></div>

    <div class="grid grid-cols-3 gap-2 mb-4 text-center">
        <div class="card p-3"><p class="text-[9px] text-slate-400 uppercase font-bold">মোট ট্রেড</p><p id="t_trades" class="text-lg font-black">0</p></div>
        <div class="card p-3"><p class="text-[9px] text-slate-400 uppercase font-bold">জয়ের হার</p><p id="w_rate" class="text-lg font-black text-green-500">0%</p></div>
        <div class="card p-3"><p class="text-[9px] text-slate-400 uppercase font-bold">মোট P&L</p><p id="t_pnl" class="text-lg font-black text-green-600">$0.00</p></div>
    </div>

    <div class="card p-5 mb-4 text-center">
        <div class="flex justify-between items-center mb-3"><span id="price" class="text-4xl font-black text-slate-900">$0.00</span><div class="text-right text-[10px] text-slate-400">ব্যালেন্স: <b id="balance" class="text-slate-700">$100.00</b></div></div>
        <div id="wait_reason" class="bg-orange-50 text-orange-600 p-2 rounded-xl text-[11px] font-bold border border-orange-100">&#8987; লোড হচ্ছে...</div>
    </div>

    <div class="card p-4 mb-3">
        <div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="sig1" class="px-2 py-0.5 rounded text-[10px] font-bold">WAIT</span></div>
        <div class="flex justify-between text-[11px] text-slate-500 mb-2"><span>RSI: <b id="rsi1" class="text-slate-800">0</b></span><span>EMA20: <b id="ema20" class="text-slate-800">0</b></span></div>
        <div id="pats1" class="flex flex-wrap gap-1"></div>
    </div>

    <div class="card p-4 mb-3">
        <div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="sig3" class="px-2 py-0.5 rounded text-[10px] font-bold">WAIT</span></div>
        <div class="flex justify-between text-[11px] text-slate-500 mb-2"><span>RSI: <b id="rsi3" class="text-slate-800">0</b></span><span>MACD: <b id="macd3" class="text-slate-800">0</b></span></div>
        <div id="pats3" class="flex flex-wrap gap-1"></div>
    </div>

    <div class="card overflow-hidden h-56 mb-4"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>

    <div class="card p-4">
        <h3 class="font-bold text-slate-700 text-xs mb-3">&#128203; লাইভ লগ</h3>
        <div id="logs" class="space-y-1"></div>
    </div>
</div>

<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            document.getElementById('price').innerText = '$' + d.price;
            document.getElementById('balance').innerText = '$' + d.balance.toFixed(2);
            document.getElementById('t_trades').innerText = d.total_trades;
            document.getElementById('w_rate').innerText = d.win_rate + '%';
            document.getElementById('t_pnl').innerText = '$' + d.total_pnl.toFixed(2);
            document.getElementById('wait_reason').innerText = d.wait_reason;
            document.getElementById('rsi1').innerText = d.analysis_1m.rsi;
            document.getElementById('ema20').innerText = d.analysis_1m.ema20;
            document.getElementById('sig1').innerText = d.analysis_1m.signal;
            document.getElementById('sig1').className = d.analysis_1m.signal === 'বুলিশ' ? 'bg-green-100 text-green-600 px-2 py-0.5 rounded text-[10px] font-bold' : 'bg-red-100 text-red-600 px-2 py-0.5 rounded text-[10px] font-bold';
            document.getElementById('rsi3').innerText = d.analysis_3m.rsi;
            document.getElementById('macd3').innerText = d.analysis_3m.macd;
            document.getElementById('sig3').innerText = d.analysis_3m.signal;
            document.getElementById('sig3').className = d.analysis_3m.signal === 'বুলিশ' ? 'bg-blue-100 text-blue-600 px-2 py-0.5 rounded text-[10px] font-bold' : 'bg-slate-100 text-slate-500 px-2 py-0.5 rounded text-[10px] font-bold';
            document.getElementById('pats1').innerHTML = d.analysis_1m.patterns.map(p => `<span class="tag">${p}</span>`).join('');
            document.getElementById('pats3').innerHTML = d.analysis_3m.patterns.map(p => `<span class="tag">${p}</span>`).join('');
            document.getElementById('logs').innerHTML = d.log.slice(0,3).map(l => `<p class="text-[10px] text-slate-500 border-b border-slate-50 pb-1">${l.time} - ${l.msg}</p>`).join('');
        } catch(e) {}
    }
    setInterval(update, 5000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))