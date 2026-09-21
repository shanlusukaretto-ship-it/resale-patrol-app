import streamlit as st, datetime as dt, urllib.parse as up, json, time, feedparser, re, urllib.request
from google import genai
from supabase import create_client

st.set_page_config(page_title="プレ値パトロール", page_icon="🎯", layout="wide")
gk = st.secrets.get("GEMINI_API_KEY", "")
su, sk = st.secrets.get("SUPABASE_URL", ""), st.secrets.get("SUPABASE_KEY", "")
sb = create_client(su, sk) if su and sk else None
if "items_list" not in st.session_state: st.session_state.items_list = []

ICONS = {"TCG":"🃏","プレバン":"🤖","スニーカー":"👟","コフレ":"💄","ホビー":"🧸","釣具":"🎣"}
ST_OPTS = ["未応募", "応募中", "当選", "落選"]

def p_num(v, d=0):
    c = re.sub(r"[^\d\-]", "", str(v)) if v is not None else ""
    return int(c) if c else d

def clean_u(u):
    m = re.search(r'https?://[^\s)\]"]+', str(u or ""))
    return m.group(0) if m else str(u or "").strip()

def load_db():
    if sb:
        try:
            r = sb.table("items").select("*").order("id", desc=True).execute().data
            if isinstance(r, list): return r
        except: pass
    return st.session_state.items_list

def save_db(it):
    if sb:
        try: sb.table("items").insert(it).execute(); return
        except: pass
    st.session_state.items_list = [it] + [x for x in st.session_state.items_list if x.get("id") != it.get("id")]

def update_db(i_id, d):
    if sb:
        try: sb.table("items").update(d).eq("id", i_id).execute()
        except: pass

def del_db(i_id):
    if sb:
        try: sb.table("items").delete().eq("id", i_id).execute()
        except: pass
    st.session_state.items_list = [x for x in st.session_state.items_list if str(x.get("id")) != str(i_id)]

def fetch_txt(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as res:
            return ' '.join(re.sub(r'<[^>]+>', ' ', res.read().decode('utf-8', errors='ignore')).split())[:1200]
    except: return ""

def get_links(n):
    e = up.quote(n)
    return [{"site_name": "あみあみ予約", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={e}", "deadline_date": None, "status": "未応募"},
            {"site_name": "スニダン相場", "url": f"https://snkrdunk.com/search?keywords={e}", "deadline_date": None, "status": "未応募"}]

def call_ai(txt):
    if not gk: return None
    try:
        p = f"限定品アナリストとして商品名,定価(不明なら0),予想相場(不明なら0),締切(YYYY-MM-DD/null),URL,ジャンル(TCG/プレバン/スニーカー/コフレ/ホビー/釣具/その他)をJSON出力。文:{txt[:1500]}\n形式:{{\"name\":\"商品名\",\"rp\":0,\"mp\":0,\"dl\":null,\"url\":null,\"genre\":\"ホビー\"}}"
        r = genai.Client(api_key=gk).models.generate_content(model="gemini-2.5-flash", contents=p)
        return json.loads(r.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def add_card(name, url, genre="ホビー", rp=0, mp=0, dl=None, is_tea=True):
    td = dt.date.today().strftime("%Y-%m-%d")
    u = clean_u(url) or "https://google.com"
    pf = mp - int(mp * 0.1) - 750 - rp if (mp and rp) else 0
    sites = [{"site_name": "公式・特設", "url": u, "deadline_date": dl, "status": "未応募"}] + get_links(name)
    save_db({"id": str(int(time.time()*1000)), "name": name, "url": u, "retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": round((pf/mp)*100,1) if mp>0 else 0, "break_even": int((rp+750)/0.9) if rp else 0, "sns_genre": genre, "is_teaser": is_tea, "trust_score": 90, "trust_reason": "登録", "created_at": td, "updated_at": td, "sites": sites})

st.subheader("🎯 プレ値パトロール")
today_d = dt.date.today()
today = today_d.strftime("%Y-%m-%d")

# 1. 登録フォーム
with st.container(border=True):
    st.write("⚡ **Xポスト / 特設LP をAI即時登録**")
    with st.form("add_form", clear_on_submit=True):
        raw_in = st.text_area("X投稿文 または 特設サイトURLを貼り付け", height=70, placeholder="例: https://... や 【予約開始】限定グッズ発売！定価3500円...")
        if st.form_submit_button("🚀 AI解析してリストに追加", use_container_width=True) and raw_in.strip():
            with st.spinner("AI解析中..."):
                u = clean_u(raw_in)
                txt = (fetch_txt(u) or raw_in) if u.startswith("http") else raw_in
                d = call_ai(txt) or {}
                nm = d.get("name") or (up.urlparse(u).netloc if u.startswith("http") else "注目アイテム")
                rp, mp = p_num(d.get("rp")), p_num(d.get("mp"))
                add_card(nm, d.get("url") or u, d.get("genre","ホビー"), rp, mp, d.get("dl"), is_tea=(rp==0 and mp==0))
                st.toast(f"✅ 追加完了: {nm}")
                time.sleep(0.3); st.rerun()

# 2. 自動巡回
if st.button("🔄 Google News 自動巡回", use_container_width=True):
    qs = ["クリスマスコフレ 予約 抽選", "ポケカ 抽選予約", "ワンピースカード 抽選", "プレバン 受注開始", "Nike SNKRS 抽選"]
    hits = []
    for q in qs:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={up.quote(q)}&hl=ja&gl=JP&ceid=JP:ja")
        for e in f.entries[:2]: hits.append((e.title, e.link))
    for title, link in hits:
        d = call_ai(title) or {}
        add_card(d.get("name") or title[:25], link, d.get("genre","ホビー"), p_num(d.get("rp")), p_num(d.get("mp")), d.get("dl"), is_tea=(p_num(d.get("rp"))==0))
    st.success("巡回完了！"); st.rerun()

# 3. 絞り込み・ソート
raw_items = [x for x in load_db() if x.get("sns_genre") != "カスタムRSS"]
norm_items = []
for it in raw_items:
    s = it.get("sites", [])
    if isinstance(s, str):
        try: s = json.loads(s)
        except: s = []
    it["sites"] = s
    norm_items.append(it)

c_f1, c_f2 = st.columns(2)
with c_f1:
    s_gen = st.selectbox("ジャンル", ["すべて", "💄 コフレ", "🃏 TCG", "🤖 プレバン", "👟 スニーカー", "🧸 ホビー", "🎣 釣具"])
    f_app = st.checkbox("【応募中】のみ表示")
with c_f2:
    s_srt = st.selectbox("並び順", ["更新順", "利益額順", "利益率順", "相場順", "締切順"])

fil = [x for x in norm_items if (s_gen == "すべて" or (s_gen.split()[-1] in x.get("sns_genre",""))) and (not f_app or any(s.get("status")=="応募中" for s in x.get("sites",[])))]
if s_srt == "利益額順": fil.sort(key=lambda x: p_num(x.get("profit")), reverse=True)
elif s_srt == "利益率順": fil.sort(key=lambda x: float(x.get("margin_rate") or 0), reverse=True)
elif s_srt == "相場順": fil.sort(key=lambda x: p_num(x.get("market_price")), reverse=True)
elif s_srt == "締切順": fil.sort(key=lambda x: min([s.get("deadline_date") for s in x.get("sites",[]) if s.get("deadline_date")] or ["9999-99-99"]))
else: fil.sort(key=lambda x: str(x.get("updated_at","")), reverse=True)

st.caption(f"📦 パトロール対象: **{len(fil)}** 件")

# 4. カード一覧
for it in fil:
    sites = it.get("sites", [])
    has_app = any(s.get("status") == "応募中" for s in sites)
    is_tea = it.get("is_teaser", False) or (p_num(it.get('retail_price')) == 0 and p_num(it.get('market_price')) == 0)
    pv = p_num(it.get('profit'), 0)
    badge = "📢【先行予告】" if is_tea else (f"💰 +{pv:,}円" if pv >= 10000 else "")
    icon = ICONS.get(it.get("sns_genre",""), "📦")

    with st.expander(f"{icon} {it.get('name','')} {badge} {'【応募中】' if has_app else ''}"):
        ci, cd = st.columns([4, 1])
        ci.caption(f"ジャンル: {it.get('sns_genre','-')} | 更新: {it.get('updated_at','-')}")
        if cd.button("削除", key=f"d_{it['id']}"): del_db(it["id"]); st.rerun()

        m1, m2 = st.columns(2)
        m1.metric("定価", f"¥{p_num(it.get('retail_price')):,}" if not is_tea else "未発表")
        m2.metric("相場", f"¥{p_num(it.get('market_price')):,}" if not is_tea else "追跡中")
        m3, m4 = st.columns(2)
        m3.metric("利益", f"¥{pv:,}" if not is_tea else "-", f"{it.get('margin_rate',0)}%" if not is_tea else "")
        m4.metric("損益分岐", f"¥{p_num(it.get('break_even')):,}" if not is_tea else "-")

        cx1, cx2 = st.columns(2)
        with cx1:
            with st.popover("𝕏 告知文面", use_container_width=True):
                hk = "【遊戯王ファン必見🚨】" if "遊戯王" in it.get("name","") else ("【ポケカ速報🚨】" if "ポケ" in it.get("name","") else "【注目速報📢】")
                tw_main = f"{hk}\n注目の限定・コラボ情報です！\n\n📦 {it.get('name','')}\n・定価目安: ¥{p_num(it.get('retail_price')):,}\n\n見逃し防止に【ブックマーク🔖】推奨！\n\n👇 詳細はリプ欄へ\n#{it.get('sns_genre','限定品')} #定価確保"
                st.text_area("本文", tw_main, height=110, key=f"tm_{it['id']}")
                st.link_button("👉 𝕏 投稿画面へ", f"https://twitter.com/intent/tweet?text={up.quote(tw_main)}", use_container_width=True)
                st.text_area("リプ用", "【公式・受付元】\n" + clean_u(it.get("url")), height=60, key=f"tr_{it['id']}")
        with cx2:
            if st.button("🔄 相場再取得", key=f"r_{it['id']}", use_container_width=True):
                txt = fetch_txt(clean_u(it.get("url"))) or it.get("name","")
                d = call_ai(txt) or {}
                rp, mp = p_num(d.get("rp"), p_num(it.get('retail_price'))), p_num(d.get("mp"), p_num(it.get('market_price')))
                pf = mp - int(mp * 0.1) - 750 - rp if (mp and rp) else 0
                update_db(it["id"], {"retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": round((pf/mp)*100,1) if mp>0 else 0, "break_even": int((rp+750)/0.9) if rp else 0, "is_teaser": (rp==0 and mp==0), "updated_at": today})
                st.rerun()

        kw = up.quote(it.get("name",""))
        k1, k2, k3 = st.columns(3)
        k1.link_button("👟 スニダン", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
        k2.link_button("🔴 メルカリ", f"https://jp.mercari.com/search?keyword={kw}&status=sold_out", use_container_width=True)
        k3.link_button("🇺🇸 eBay", f"https://www.ebay.com/sch/i.html?_nkw={kw}&LH_Complete=1&LH_Sold=1", use_container_width=True)

        for idx, s in enumerate(sites):
            with st.container(border=True):
                st.write(f"🔗 **{s.get('site_name','サイト')}** (締切: `{s.get('deadline_date') or '未設定'}`)")
                cs_st, cs_dt = st.columns(2)
                cur_st = s.get("status", "未応募")
                nst = cs_st.selectbox("状況", ST_OPTS, index=ST_OPTS.index(cur_st) if cur_st in ST_OPTS else 0, key=f"st_{it['id']}_{idx}")
                d_val = dt.datetime.strptime(str(s.get("deadline_date"))[:10], "%Y-%m-%d").date() if s.get("deadline_date") else today_d
                nd = cs_dt.date_input("締切編集", value=d_val, key=f"dt_{it['id']}_{idx}").strftime("%Y-%m-%d")
                if nst != cur_st or (s.get("deadline_date") and nd != s.get("deadline_date")):
                    s["status"], s["deadline_date"], s["updated_at"] = nst, nd, today
                    update_db(it["id"], {"sites": sites, "updated_at": today}); st.rerun()
                st.link_button("👉 サイトを開く", clean_u(s.get("url", "https://google.com")), use_container_width=True)
