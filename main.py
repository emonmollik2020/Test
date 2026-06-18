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
DEFAULT_TP = 0.007    # ০.৭% লক্ষ্য
DEFAULT_SL = 0.010    # ১.০% স্টপ লস
exchange = ccxt.bitget({'enableRateLimit': True})

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "price": 0.0, "balance": 100.0, "total_pnl": 0.0, "last_update": "...",
            "trades": 0, "win_rate": 0.0, "best": 0.0, "worst": 0.0, "last_action": "---",
            "in_position": False, "live_pnl_pct": 0.0, "live_pnl_val": 0.0, "entry_price": 0.0,
            "tp_level": 0.0, "sl_level": 0.0, "peak_price": 0.0,
            "analysis_1m": {"rsi": 0, "ema20": 0, "ema50": 0, "sig": "WAIT", "pats": []},
            "analysis_3m": {"rsi": 0, "macd": 0, "sig": "WAIT", "pats": []},
            "wait_reason": "বিশ্লেষণ শুরু...", "log": [], "history": []
        }
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            if "history" not in data: data["history"] = []
            return data
    except: return load_state()

app = Flask(__name__)

# --- অ্যাডভান্সড ১৬-প্যাটার্ন ডিটেক্টর ---
def detect_advanced_pats(df):
    p = []
    if len(df) < 5: return p
    c1, c2, c3 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    def parts(c):
        body = abs(c['c'] - c['o'])
        total = max(0.001, c['h'] - c['l'])
        u_wick = c['h'] - max(c['c'], c['o'])
        l_wick = min(c['c'], c['o']) - c['l']
        return body, total, u_wick, l_wick, (c['c'] > c['o'])
    b1, t1, u1, l1, g1 = parts(c1)
    b2, t2, u2, l2, g2 = parts(c2)
    b3, t3, u3, l3, g3 = parts(c3)
    if b1 > 0 and l1 >= 2 * b1 and u1 <= 0.2 * b1: p.append({"n": "হ্যামার &#128296;", "t": "bull"})
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: p.append({"n": "ইনভার্টেড হ্যামার &#128296;", "t": "bull"})
    if not g2 and g1 and c1['c'] >= c2['o'] and c1['o'] <= c2['c']: p.append({"n": "বুলিশ এনগালফিং &#128200;", "t": "bull"})
    if b1 / t1 > 0.85 and g1: p.append({"n": "বুল মারুবোজু 💪", "t": "bull"})
    if not g3 and b2 < (b3 * 0.3) and g1 and c1['c'] > (c3['o'] + c3['c']) / 2: p.append({"n": "মর্নিং স্টার &#127749;", "t": "bull"})
    if b1 > 0 and u1 >= 2 * b1 and l1 <= 0.2 * b1: p.append({"n": "শুটিং স্টার &#9732;", "t": "bear"})
    if g2 and not g1 and c1['c'] <= c2['o'] and c1['o'] >= c2['c']: p.append({"n": "বেয়ারিশ এনগালফিং &#128201;", "t": "bear"})
    if b1 <= (t1 * 0.1): p.append({"n": "ডোজি &#9878;", "t": "neut"})
    return p

# --- ট্রেডিং ইঞ্জিন লজিক ---
def bot_engine():
    pnl_history = []
    in_pos, holdings, entry_p, total_pnl_acc = False, 0.0, 0.0, 0.0
    tp_level, sl_level, peak_p = 0.0, 0.0, 0.0
    wins, total = 0, 0

    while True:
        try:
            bars1 = exchange.fetch_ohlcv(SYMBOL, '1m', limit=200)
            bars3 = exchange.fetch_ohlcv(SYMBOL, '3m', limit=200)
            df1, df3 = pd.DataFrame(bars1, columns=['t','o','h','l','c','v']), pd.DataFrame(bars3, columns=['t','o','h','l','c','v'])
            p = df1['c'].iloc[-1]
            r1 = ta.momentum.rsi(df1['c']).fillna(0).iloc[-1]
            e20 = ta.trend.ema_indicator(df1['c'], window=20).fillna(0).iloc[-1]
            e50 = ta.trend.ema_indicator(df1['c'], window=50).fillna(0).iloc[-1]
            macd = ta.trend.MACD(df3['c'])
            cur = load_state()
            live_pnl_pct = ((p / entry_p) - 1) * 100 if in_pos else 0.0
            live_pnl_val = (100.0 / entry_p * p) - 100.0 if in_pos else 0.0

            if in_pos and p > peak_p:
                peak_p = p
                sl_level = round(peak_p * (1 - DEFAULT_SL), 2)
                tp_level = round(peak_p * (1 + DEFAULT_TP), 2)

            cur.update({
                "price": round(p, 2), "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "in_position": in_pos, "live_pnl_pct": round(live_pnl_pct, 2), "live_pnl_val": round(live_pnl_val, 2),
                "entry_price": round(entry_p, 2), "tp_level": tp_level, "sl_level": sl_level,
                "analysis_1m": {"rsi": round(r1,1), "ema20": round(e20,2), "ema50": round(e50,2), "sig": "বুলিশ ✅" if p > e20 else "বেয়ারিশ ❌", "pats": detect_advanced_pats(df1)},
                "analysis_3m": {"rsi": round(ta.momentum.rsi(df3['c']).fillna(0).iloc[-1],1), "macd": round(macd.macd().iloc[-1],3), "sig": "বুলিশ ✅" if macd.macd().iloc[-1] > macd.macd_signal().iloc[-1] else "অপেক্ষা", "pats": detect_advanced_pats(df3)},
                "wait_reason": "ট্রেড লাইভ (ট্রেলিং সক্রিয়)" if in_pos else "এন্ট্রি খুঁজছে..."
            })

            if not in_pos and p > e20 and r1 < 65 and macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]:
                entry_p, peak_p, in_pos, total = p, p, True, total + 1
                sl_level, tp_level = round(p * (1 - DEFAULT_SL), 2), round(p * (1 + DEFAULT_TP), 2)
                cur.update({"trades": total, "balance": 0.0, "last_action": "BUY"})
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🟢 BUY @ ${p:.2f}"})
                cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "BUY", "p": round(p, 2), "r": "---"})
            elif in_pos and (p >= tp_level or p <= sl_level):
                in_pos = False
                total_pnl_acc += live_pnl_val
                pnl_history.append(live_pnl_val)
                if p > entry_p: wins += 1
                cur.update({"balance": round(100.0 + total_pnl_acc, 2), "total_pnl": round(total_pnl_acc, 2), "win_rate": round((wins/total)*100, 1), "best": round(max(pnl_history), 2), "last_action": "SELL"})
                cur["log"].insert(0, {"t": datetime.now().strftime("%H:%M"), "m": f"🔴 SELL @ ${round(p,2)} ({round(live_pnl_pct,2)}%)"})
                cur["history"].insert(0, {"t": datetime.now().strftime("%H:%M"), "a": "SELL", "p": round(p, 2), "r": f"{round(live_pnl_pct,2)}%"})
            save_state(cur)
        except: pass
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
    <title>SOL Pro Master</title><script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: white; border-radius: 1.25rem; border: 1px solid #f1f5f9; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .tag { border: 1px solid #dcfce7; color: #166534; padding: 2px 10px; border-radius: 99px; font-size: 11px; font-weight: 700; display: inline-block; margin: 3px; }
    </style>
</head>
<body class="p-3 text-slate-800">
<div class="max-w-md mx-auto">
    <div class="text-center mb-6"><span class="bg-green-100 text-green-700 px-4 py-1 rounded-lg text-xs font-bold border border-green-200">&#9989; বট চলছে</span></div>

    <!-- Summary Grid -->
    <div class="grid grid-cols-3 gap-2 mb-2 text-center text-[10px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>মোট ট্রেড</p><p id="t" class="text-lg font-black text-slate-800">0</p></div>
        <div class="card p-3"><p>জয়ের হার</p><p id="w" class="text-lg font-black text-slate-800">0%</p></div>
        <div class="card p-3"><p>মোট P&L</p><p id="pnl" class="text-lg font-black text-green-600">+$0.00</p></div>
    </div>
    <div class="grid grid-cols-3 gap-2 mb-4 text-center text-[9px] font-bold text-slate-400 uppercase">
        <div class="card p-3"><p>সেরা</p><p id="bt" class="text-xs font-bold text-green-500">--</p></div>
        <div class="card p-3"><p>খারাপ</p><p id="wt" class="text-xs font-bold text-red-400">--</p></div>
        <div class="card p-3"><p>শেষ</p><p id="la" class="text-xs font-bold text-slate-500">---</p></div>
    </div>

    <!-- Main Price & Live P&L -->
    <div class="card p-6 mb-4 text-center">
        <div class="flex justify-between items-center mb-4">
            <span id="pr" class="text-4xl font-black text-slate-900 tracking-tighter">$0.00</span>
            <div class="text-right text-[10px] text-slate-400">ব্যালেন্স: <b id="bl">$100.00</b></div>
        </div>
        
        <div id="pnl_display" class="hidden mb-4 p-5 border-2 rounded-3xl">
            <p class="text-[10px] font-bold text-slate-400 uppercase mb-1">লাইভ পজিশন প্রফিট</p>
            <p id="live_pnl_pct" class="text-4xl font-black">0.00%</p>
            <div class="flex justify-around mt-4 text-[10px] font-bold border-t border-slate-50 pt-2">
                <div class="text-red-500">🛑 SL: <span id="sl_level">0.0</span></div>
                <div class="text-green-600">✅ TP: <span id="tp_level">0.0</span></div>
            </div>
        </div>
        <div id="st" class="bg-orange-50 text-orange-600 p-2.5 rounded-xl text-[11px] font-bold border border-orange-100 uppercase tracking-widest italic">⌛ লোড হচ্ছে...</div>
    </div>

    <!-- Analysis -->
    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 1 মিনিট বিশ্লেষণ</h3><span id="s1" class="font-bold px-2 py-0.5 rounded bg-slate-100 text-[10px]">WAIT</span></div><div class="grid grid-cols-3 gap-y-1.5 text-slate-500 font-medium"><p>RSI: <b id="r1">0</b></p><p>E20: <b id="e20">0</b></p><p>E50: <b id="e50">0</b></p></div><div id="pats1" class="mt-3 flex flex-wrap"></div></div>
    <div class="card p-4 mb-4 text-[11px]"><div class="flex justify-between mb-3 items-center"><h3 class="font-bold text-slate-700 text-xs">&#128202; 3 মিনিট বিশ্লেষণ</h3><span id="s3" class="font-bold px-2 py-0.5 rounded bg-slate-100 text-[10px]">WAIT</span></div><div class="grid grid-cols-2 gap-y-1.5 text-slate-500 font-medium"><p>RSI: <b id="r3">0</b></p><p>MACD: <b id="m3">0</b></p></div><div id="pats3" class="mt-3 flex flex-wrap"></div></div>

    <div class="card overflow-hidden h-60 mb-4 border border-slate-100"><iframe src="https://s.tradingview.com/widgetembed/?symbol=BITGET%3ASOLUSDT&interval=1&theme=light" width="100%" height="100%" frameborder="0"></iframe></div>

    <!-- NEW: Trade History Widget -->
    <div class="card p-4 mb-4 overflow-hidden">
        <h3 class="font-bold text-slate-700 text-xs mb-3 uppercase tracking-wider">&#128203; ট্রেড হিস্ট্রি</h3>
        <div class="overflow-x-auto"><table class="w-full text-[10px] text-left"><thead class="text-slate-400 border-b"><tr><th class="pb-2">সময়</th><th class="pb-2 text-center">ধরন</th><th class="pb-2 text-right">মূল্য</th><th class="pb-2 text-right">P&L</th></tr></thead><tbody id="hist_body" class="divide-y divide-slate-50"></tbody></table></div>
    </div>

    <!-- Live Logs -->
    <div class="card p-4 mb-6"><h3 class="font-bold text-slate-700 text-xs mb-2 uppercase tracking-widest">&#128214; লাইভ লগ</h3><div id="lg" class="space-y-1 text-[10px]"></div></div>
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
                document.getElementById('st').innerText = d.wait_reason;
                
                if(d.in_position) {
                    const disp = document.getElementById('pnl_display'); disp.classList.remove('hidden');
                    document.getElementById('live_pnl_pct').innerText = (d.live_pnl_pct >= 0 ? '+' : '') + d.live_pnl_pct + '%';
                    document.getElementById('sl_level').innerText = d.sl_level; document.getElementById('tp_level').innerText = d.tp_level;
                    const col = d.live_pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
                    const bor = d.live_pnl_pct >= 0 ? 'border-green-200' : 'border-red-200';
                    document.getElementById('live_pnl_pct').className = 'text-4xl font-black ' + col;
                    disp.className = 'mb-4 p-5 border-2 rounded-3xl text-center ' + bor;
                } else { document.getElementById('pnl_display').classList.add('hidden'); }

                document.getElementById('r1').innerText = d.analysis_1m.rsi;
                document.getElementById('e20').innerText = '$' + d.analysis_1m.ema20;
                document.getElementById('e50').innerText = '$' + d.analysis_1m.ema50;
                const s1 = document.getElementById('s1'); s1.innerText = d.analysis_1m.sig;
                s1.className = d.analysis_1m.sig.includes('বুলিশ') ? 'font-bold px-2 py-0.5 rounded bg-green-100 text-green-600' : 'font-bold px-2 py-0.5 rounded bg-red-50 text-red-400';
                document.getElementById('r3').innerText = d.analysis_3m.rsi;
                document.getElementById('m3').innerText = d.analysis_3m.macd;
                const s3 = document.getElementById('s3'); s3.innerText = d.analysis_3m.sig;
                s3.className = d.analysis_3m.sig.includes('বুলিশ') ? 'font-bold px-2 py-0.5 rounded bg-green-100 text-green-600' : 'font-bold px-2 py-0.5 rounded bg-red-100 text-red-600';

                const tag = (p) => `<span class="tag">${p.n}</span>`;
                document.getElementById('pats1').innerHTML = d.analysis_1m.pats.map(tag).join('');
                document.getElementById('pats3').innerHTML = d.analysis_3m.pats.map(tag).join('');
                
                // History Update
                document.getElementById('hist_body').innerHTML = d.history.slice(0,5).map(h => `<tr><td class="py-2 text-slate-400">${h.t}</td><td class="py-2 text-center font-bold ${h.a=='BUY'?'text-green-600':'text-red-600'}">${h.a}</td><td class="py-2 text-right font-medium">$${h.p}</td><td class="py-2 text-right font-black ${h.r.includes('-')?'text-red-500':'text-green-500'}">${h.r}</td></tr>`).join('');
                document.getElementById('lg').innerHTML = d.log.slice(0,3).map(l => `<div class="flex justify-between font-bold text-slate-500 pb-1"><span>${l.t}</span><span>${l.m}</span></div>`).join('');
            }
        } catch(e) {}
    }
    setInterval(update, 3000); update();
</script>
</body></html>
