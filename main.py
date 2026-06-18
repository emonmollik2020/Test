import ccxt
import pandas as pd
import ta
import time
import threading
import json
import os
from flask import Flask, render_template_string, jsonify
from datetime import datetime, timezone

# ========== ১. কনফিগারেশন ও সেটিংস ==========
SYMBOL = "SOL/USDT"
STATE_FILE = "bot_state.json"
INITIAL_FUND = 100.0
TP_PCT = 0.007    # ০.৭% টেক প্রফিট
SL_PCT = 0.010    # ১.০% স্টপ লস
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "price": 0.0, "balance": 100.0, "total_pnl": 0.0, "last_update": "...",
            "trades": 0, "win_rate": 0, "best": 0.0, "worst": 0.0, "last_action": "---",
            "in_position": False, "live_pnl": 0.0, "entry_price": 0.0,
            "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "bu": 0, "bl": 0, "sig": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "ms": 0, "mh": 0, "sk": 0, "sd": 0, "sig": "WAIT", "pats": []},
            "wait_reason": "বিশ্লেষণ শুরু হচ্ছে...", "log": []
        }
    with open(STATE_FILE, "r") as f:
        try: return json.load(f)
        except: return load_state()

app = Flask(__name__)

# --- প্যাটার্ন ডিটেকশন ---
def detect_pats(df):
    p = []
    if len(df) < 5: return p
    o, h, l, c = df['o'].iloc[-1], df['h'].iloc[-1], df['l'].iloc[-1], df['c'].iloc[-1]
    po, pc = df['o'].iloc[-2], df['c'].iloc[-2]
    body = abs(c - o)
    full = h - l if h != l else 0.001
    if body/full > 0.85 and c > o: p.append("বুলিশ মারুবোজু &#128170;")
    if body <= (full * 0.1): p.append("ডোজি &#9878;")
    if pc < po and c > o and c >= po and o <= pc: p.append("বুলিশ এনগালফিং &#128200;")
    if body > 0 and (min(c,o)-l) >= 1.8*body: p.append("হ্যামার &#128296;")
    return p

# --- ট্রেডিং ইঞ্জিন লজিক ---
def bot_engine():
    pnl_history = []
    in_pos, holdings, entry_p, total_pnl_acc = False, 0.0, 0.0, 0.0
    wins, total = 0, 0

    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=150)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=150)
            df1 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v'])
            df3 = pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            p = df1['c'].iloc[-1]
            
            # Indicators
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            e50 = ta.trend.ema_indicator(df1['c'], window=50).fillna(0).iloc[-1]
            bb = ta.volatility.BollingerBands(df1['c'])
            r3 = ta.momentum.rsi(df3['c']).fillna(0).iloc[-1]
            m_obj = ta.trend.MACD(df3['c'])
            st_obj = ta.momentum.StochRSIIndicator(df3['c'])

            cur = load_state()
            live_pnl = ((p / entry_p) - 1) * 100 if in_pos else 0.0
            
            cur.update({
                "price": round(p, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos, "live_pnl": round(live_pnl, 2), "entry_price": round(entry_p, 2),
                "analysis_1m": {
                    "rsi": round(r1,1), "ema20": round(e20,2), "ema50": round(e50,2),
                    "bu": round(bb.bollinger_hband().iloc[-1],2), "bl": round(bb.bollinger_lband().iloc[-1],2),
                    "sig": "বুলিশ ✅" if p > e20 else "বেয়ারিশ ❌", "pats": detect_pats(df1)
                },
                "analysis_3m": {
                    "rsi": round(r3,1), "macd": round(m_obj.macd().iloc[-1],3), "ms": round(m_obj.macd_signal().iloc[-1],3),
                    "mh": round(m_obj.macd_diff().iloc[-1],3), "sk": round(st_obj.stochrsi_k().iloc[-1]*100,1),
                    "sd": round(st_obj.stochrsi_d().iloc[-1]*100,1), "sig": "বুলিশ ✅" if m_obj.macd().iloc[-1] > m_obj.macd_signal().iloc[-1] else "বেয়ারিশ ❌",
                    "pats": detect_pats(df3)
                }
            })

            if not in_pos:
                if p > e20 and r1 < 65 and m_obj.macd().iloc[-1] > m_obj.macd_signal().iloc[-1]:
                    entry_p, in_pos, total = p, True, total + 1
                    cur.update({"trades": total, "balance": 0.0, "last_action": "BUY"})
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY @ ${p:.2f}"})
                cur["wait_reason"] = "অপেক্ষায় — ১মি দুর্বল • ৩মি সিগন্যাল নেই" if p <= e20 else "এন্ট্রি খুঁজছে..."
            elif in_pos:
                cur["wait_reason"] = "ট্রেড লাইভ আছে"
                if live_pnl >= 0.7 or live_pnl <= -1.0:
                    in_pos = False
                    p_val = ((100.0 / entry_p) * p) - 100.0
                    total_pnl_acc += p_val
                    pnl_history.append(p_val)
                    if live_pnl > 0: wins += 1
                    cur.update({"balance": 100.0 + total_pnl_acc, "total_pnl": round(total_pnl_acc, 2), "win_rate": round((wins/total)*100, 1), "best": round(max(pnl_history), 2), "worst": round(min(pnl_history), 2), "last_action": "SELL"})
                    cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SELL @ ${round(p,2)} ({round(live_pnl,2)}%)"})
            save_state(cur)
        except Exception as e: print(f"Engine Error: {e}")
        time.sleep(10)

threading.Thread(target=bot_engine, daemon=True).start()

@app.route('/api/data')
def api(): return jsonify(load_state())

@app.route('/')
def index(): return render_template_string(UI)

UI = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pro SOL Bot</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { background: #dcfce7; color: #166534; padding: 2px 8px; border-radius: 99px; font-size: 10px; font-weight: 700; border: 1px solid #dcfce7; }
    </style>
</head>
<body class="p-3">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6"><span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span></div>
    <div class="grid grid-cols-3 gap-2 mb-2 text-center text-[10px] font-bold text-slate-400">
        <div class="card p-3"><p>মোট ট্রেড</p><p id="t" class="text-lg font-black text-slate-800">0</p></div>
        <div class="card p-3"><p>জয়ের হার</p><p id="w" class="text-lg font-black text-slate-800">0%</p></div>
        <div class="card p-3"><p>মোট P&L</p><p id="pnl" class="text-lg font-black text-green-600">+$0.00</p></div>
    </div>
    <div class="grid grid-cols-3 gap-2 mb-4 text-center text-[9px] font-bold text-slate-400">
        <div class="card p-3"><p>সেরা</p><p id="bt" class="text-xs font-bold text-green-400">--</p></div>
        <div class="card p-3"><p>খারাপ</p><p id="wt" class="text-xs font-bold text-red-400">--</p></div>
        <div class="card p-3"><p>শেষ</p><p id="la" class="text-xs font-bold text-slate-500">---</p></div>
    </div>
    <div class="card p-6 mb-4">
        <div class="flex justify-between items-center mb-4">
            <span id="pr" class="text-4xl font-black text-slate-900">$0.00</span>
            <div class="text-right text-[10px] text-slate-400">ব্যালেন্স: <b id="bl" class="text-slate-700">$100.00</b><br>P&L: <b id="lpnl" class="text-green-600">+$0.00</b></div>
        </div>
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 text-center uppercase tracking-wide">⌛ লোড হচ্ছে...</div>
    </div>
    <div class="card p-4 mb-4">
        <div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="s1" class="text-[10px] font-bold px-2 py-0.5 rounded">WAIT</span></div>
        <div class="grid grid-cols-2 gap-y-2 text-[11px] text-slate-500">
            <div class="flex justify-between px-1"><span>RSI (14)</span><b id="r1">0</b></div>
            <div class="flex justify-between px-1"><span>EMA 20</span><b id="e20">0</b></div>
            <div class="flex justify-between px-1"><span>EMA 50</span><b id="e50">0</b></div>
            <div class="flex justify-between px-1"><span>BB উপর</span><b id="bu">0</b></div>
            <div class="flex justify-between px-1"><span>BB নিচ</span><b id="bl_1">0</b></div>
        </div>
        <div id="pats1" class="mt-3 flex flex-wrap gap-1"></div>
    </div>
    <div class="card p-4 mb-4">
        <div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="s3" class="text-[10px] font-bold px-2 py-0.5 rounded">WAIT</span></div>
        <div class="grid grid-cols-2 gap-y-2 text-[11px] text-slate-500">
            <div class="flex justify-between px-1"><span>RSI (14)</span><b id="r3">0</b></div>
            <div class="flex justify-between px-1"><span>MACD</span><b id="m3">0</b></div>
            <div class="flex justify-between px-1"><span>MACD Sig</span><b id="ms3">0</b></div>
            <div class="flex justify-between px-1"><span>MACD Hist</span><b id="mh3">0</b></div>
            <div class="flex justify-between px-1"><span>Stoch K</span><b id="sk3">0</b></div>
            <div class="flex justify-between px-1"><span>Stoch D</span><b id="sd3">0</b></div>
        </div>
        <div id="pats3" class="mt-3 flex flex-wrap gap-1"></div>
    </div>
    <div class="card overflow-hidden h-56 mb-4"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>
    <div class="card p-4 mb-6"><h3 class="font-bold text-slate-700 text-xs mb-2">&#128203; লাইভ লগ</h3><div id="lg" class="space-y-1 text-[10px]"></div></div>
</div>
<script>
    async function update() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            if(d.price > 0) {
                document.getElementById('pr').innerText = '$' + d.price;
                document.getElementById('bl').innerText = '$' + d.balance.toFixed(2);
                document.getElementById('t').innerText = d.trades;
                document.getElementById('w').innerText = d.win_rate + '%';
                document.getElementById('pnl').innerText = (d.total_pnl >= 0 ? '+$' : '$') + d.total_pnl.toFixed(2);
                document.getElementById('bt').innerText = '$' + d.best.toFixed(2);
                document.getElementById('wt').innerText = '$' + d.worst.toFixed(2);
                document.getElementById('la').innerText = d.last_action;
                document.getElementById('st').innerText = '⌛ ' + d.wait_reason;
                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e20').innerText = '$' + d.analysis_1m.ema20;
                document.getElementById('e50').innerText = '$' + d.analysis_1m.ema50;
                document.getElementById('bu').innerText = '$' + d.analysis_1m.bu;
                document.getElementById('bl_1').innerText = '$' + d.analysis_1m.bl;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.sig;
                s1.className = d.analysis_1m.sig.includes('বুলিশ') ? 'text-[10px] font-bold px-2 py-0.5 rounded bg-green-100 text-green-600' : 'text-[10px] font-bold px-2 py-0.5 rounded bg-red-100 text-red-600';
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                document.getElementById('ms3').innerText = d.analysis_3m.ms;
                document.getElementById('mh3').innerText = d.analysis_3m.mh;
                document.getElementById('sk3').innerText = d.analysis_3m.sk;
                document.getElementById('sd3').innerText = d.analysis_3m.sd;
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.sig;
                s3.className = d.analysis_3m.sig.includes('বুলিশ') ? 'text-[10px] font-bold px-2 py-0.5 rounded bg-green-100 text-green-600' : 'text-[10px] font-bold px-2 py-0.5 rounded bg-red-100 text-red-600';
                const tag = (p) => `<span class="tag">${p.n}</span>`;
                document.getElementById('pats1').innerHTML = d.analysis_1m.pats.map(tag).join('');
                document.getElementById('pats3').innerHTML = d.analysis_3m.pats.map(tag).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between font-bold text-slate-500 pb-1"><span>${l.t}</span><span>${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 4000); update();
</script>
</body></html>
"""

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
