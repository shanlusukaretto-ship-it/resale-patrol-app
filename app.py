import streamlit as st, datetime, urllib.parse, json, time, feedparser
from google import genai
from supabase import create_client, Client

st.set_page_config(page_title="プレミア商品確認ツール", page_icon="📦", layout="wide")

gemini_key = st.secrets.get("GEMINI_API_KEY", "")
sb_url = st.secrets.get("SUPABASE_URL", "")
sb_key = st.secrets.get("SUPABASE_KEY", "")

supabase: Client = None
if sb_url and sb_key:
    try: supabase = create_client(sb_url, sb_key)
    except: pass

if "items" not in st.session_state: st.session_state.items = []

def load_db():
    if supabase:
        try: return supabase.table("items").select("*").order("id", desc=True).execute().data
        except: return st.session_state.items
    return st.session_state.items

def save_db(item):
    if supabase:
        try: supabase.table("items").insert(item).execute()
        except: st.session_state.items.append(item)
    else: st.session_state.items.append(item)

def update_db(i_id, data):
    if supabase:
        try: supabase.table("items").update(data).eq("id", i_id).execute()
        except: pass

def del_db(i_id):
    if supabase:
        try: supabase.table("items").delete().eq("id", i_id).execute()
        except: pass
    st.session_state.items = [x for x in st.session_state.items if str(x.get("id")) != str(i_id)]

def get_links(name, genre, dl):
    enc = urllib.parse.quote(name)
    g = str(genre) + str(name)
    if any(k in g for k in ["TCG", "ポケカ", "ワンピース"]):
        return [
            {"site_name": "ポケモンセンターオンライン", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={enc}", "deadline_date": dl},
            {"site_name": "あみあみ（予約抽選）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={enc}", "deadline_date": dl},
            {"site_name": "ヨドバシ・ドット・コム", "url": f"https://www.yodobashi.com/?word={enc}", "deadline_date": dl},
            {"site_name": "スニダン（相場）", "url": f"https://snkrdunk.com/search?keywords={enc}", "deadline_date": dl}
        ]
    elif any(k in g for k in ["プレバン", "バンダイ"]):
        return [
            {"site_name": "プレミアムバンダイ公式", "url": f"https://p-bandai.jp/chara/c0001/?utm_source=search&keyword={enc}", "deadline_date": dl},
            {"site_name": "あみあみ公式", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={enc}", "deadline_date": dl}
        ]
    return [
        {"site_name": "SNKRS / Nike公式", "url": f"https://www.nike.com/jp/w?q={enc}", "deadline_date": dl},
        {"site_name": "スニダン（検索）", "url": f"https://snkrdunk.com/search?keywords={enc}", "deadline_date": dl}
    ]

def analyze_ai(name, url, genre, raw):
    if not gemini_key: return None
    try:
        c = genai.Client(api_key=gemini_key)
        today = datetime.date.today().strftime("%Y-%m-%d")
        dl = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        p = f"本日は{today}。限定品の定価,相場,受付先をJSON出力。曖昧文字列禁止。対象:{name},{url},{genre},{raw[:300]}。フォーマット:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":12000,\"deadline\":\"{dl}\",\"sites\":[{{\"site_name\":\"受付元\",\"url\":\"{url}\",\"deadline\":\"{dl}\"}}]}}"
        res = c.models.generate_content(model="gemini-3.6-flash", contents=p)
        txt = res.text.strip().replace("```json","").replace("```","").strip()
        return json.loads(txt)
    except: return None

def fetch_rss():
    hits = []
    qs = [("ポケカ 抽選予約 予約開始", "TCG"), ("プレミアムバンダイ 受注開始 限定", "プレバン"), ("Nike SNKRS 抽選", "スニーカー")]
    for q, g in qs:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=ja&gl=JP&ceid=JP:ja")
        for e in f.entries[:2]:
            hits.append({"name": e.title, "url": e.link, "genre": g, "summary": getattr(e, "summary", "")})
    return hits

st.title("プレミア商品確認ツール")
c1, c2 = st.columns([1, 1])

with c1:
    if st.button("🔄 全自動で最新情報を巡回収集", use_container_width=True):
        p_text, p_bar = st.empty(), st.progress(0)
        hits = fetch_rss()
        total = len(hits) if hits else 1
        all_it = load_db()
        today = datetime.date.today().strftime("%Y-%m-%d")
        def_dl = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        
        for idx, h in enumerate(hits):
            p = int(((idx + 1) / total) * 100)
            p_text.markdown(f"**巡回中... {p}%**")
            p_bar.progress((idx + 1) / total)
            d = analyze_ai(h["name"], h["url"], h["genre"], h["summary"])
            if d:
                p_name = d.get("standard_name") or h["name"][:25]
                ret, mkt = int(d.get("retail_price", 5000)), int(d.get("market_price", 10000))
                prof = mkt - int(mkt * 0.1) - 750 - ret
                margin = round((prof / mkt) * 100, 1) if mkt > 0 else 0
                main_dl = d.get("deadline", def_dl)
                sites = [{"site_name": s.get("site_name","公式"), "url": s.get("url") or h["url"], "created_at": today, "updated_at": today, "deadline_date": s.get("deadline", main_dl), "status": "未応募"} for s in d.get("sites", [])]
                for l in get_links(p_name, h["genre"], main_dl):
                    if not any(x["site_name"] == l["site_name"] for x in sites):
                        sites.append({"site_name": l["site_name"], "url": l["url"], "created_at": today, "updated_at": today, "deadline_date": l["deadline_date"], "status": "未応募"})
                
                matched = next((x for x in all_it if p_name in x.get("name","") or x.get("name","") in p_name), None)
                if matched:
                    cs = matched.get("sites") or []
                    if isinstance(cs, str): cs = json.loads(cs)
                    names = [x.get("site_name") for x in cs]
                    for s in sites:
                        if s["site_name"] not in names: cs.append(s)
                    update_db(matched["id"], {"sites": cs, "updated_at": today})
                else:
                    rec = {"id": str(int(time.time())+idx), "name": p_name, "url": h["url"], "retail_price": ret, "market_price": mkt, "profit": prof, "margin_rate": margin, "break_even": int((ret+750)/0.9), "sns_genre": h["genre"], "created_at": today, "updated_at": today, "sites": sites}
                    save_db(rec)
                    all_it.append(rec)
            time.sleep(0.05)
        p_bar.empty(); p_text.empty()
        st.success("巡回完了！")
        st.rerun()

with c2:
    with st.popover("➕ 手動追加"):
        in_n = st.text_input("商品名")
        in_u = st.text_input("URL")
        in_g = st.selectbox("ジャンル", ["TCG", "プレバン", "スニーカー", "その他"])
        if st.button("登録", use_container_width=True):
            today = datetime.date.today().strftime("%Y-%m-%d")
            dl = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            sites = [{"site_name": "指定URL", "url": in_u or "https://google.com", "created_at": today, "updated_at": today, "deadline_date": dl, "status": "未応募"}]
            for l in get_links(in_n or "手動商品", in_g, dl): sites.append({"site_name": l["site_name"], "url": l["url"], "created_at": today, "updated_at": today, "deadline_date": dl, "status": "未応募"})
            rec = {"id": str(int(time.time())), "name": in_n or "手動商品", "url": in_u or "https://google.com", "retail_price": 5000, "market_price": 10000, "profit": 3250, "margin_rate": 32.5, "break_even": 6388, "sns_genre": in_g, "created_at": today, "updated_at": today, "sites": sites}
            save_db(rec)
            st.success("登録完了！")
            st.rerun()

st.markdown("---")

items = load_db()
today = datetime.date.today().strftime("%Y-%m-%d")
def_dl = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

norm_items = []
for it in items:
    s = it.get("sites")
    if isinstance(s, str):
        try: s = json.loads(s)
        except: s = []
    if not s:
        s = [{"site_name": "情報元", "url": it.get("url","https://google.com"), "created_at": it.get("created_at", today), "updated_at": it.get("updated_at", today), "deadline_date": def_dl, "status": "未応募"}]
        for l in get_links(it.get("name",""), it.get("sns_genre",""), def_dl):
            s.append({"site_name": l["site_name"], "url": l["url"], "created_at": today, "updated_at": today, "deadline_date": def_dl, "status": "未応募"})
        update_db(it["id"], {"sites": s})
    it["sites"] = s
    norm_items.append(it)

app_count = sum(1 for x in norm_items if any(s.get("status") == "応募中" for s in x.get("sites", [])))
f_app = st.checkbox(f"【応募中】がある商品のみ表示（現在: {app_count} 件）")

for item in norm_items:
    sites = item.get("sites", [])
    has_app = any(s.get("status") == "応募中" for s in sites)
    if f_app and not has_app: continue
    
    badge = "【応募中あり】" if has_app else ""
    p_disp = f"+{item.get('profit', 0):,}円" if item.get('profit') else ""
    
    with st.expander(f"📦 {item.get('name','')} {p_disp} {badge}"):
        ci, cd = st.columns([5, 1])
        ci.caption(f"ジャンル: {item.get('sns_genre','')} | 掲載日: {item.get('created_at','-')} | 更新日: {item.get('updated_at','-')}")
        if cd.button("削除", key=f"d_{item['id']}"):
            del_db(item["id"])
            st.rerun()
            
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("定価", f"¥{item.get('retail_price',0):,}")
        m2.metric("予想相場", f"¥{item.get('market_price',0):,}")
        m3.metric("見込利益", f"¥{item.get('profit',0):,}")
        m4.metric("損益分岐", f"¥{item.get('break_even',0):,}")
        
        kw = urllib.parse.quote(item.get("name",""))
        k1, k2 = st.columns(2)
        k1.link_button("👟 スニダン相場", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
        k2.link_button("🔴 メルカリ相場", f"https://jp.mercari.com/search?keyword={kw}&status=sold_out", use_container_width=True)
        
        st.write(f"**受付サイト一覧（{len(sites)}件）**")
        up_flag = False
        for idx, s in enumerate(sites):
            with st.container(border=True):
                st.write(f"🔗 **{s.get('site_name')}**")
                st.caption(f"初回: {s.get('created_at','-')} | 更新: {s.get('updated_at','-')} | 締切: {s.get('deadline_date','未設定')}")
                
                cs, cd = st.columns(2)
                st_list = ["未応募", "応募中", "当選", "落選"]
                cur_st = s.get("status", "未応募")
                nst = cs.selectbox("応募状況", st_list, index=st_list.index(cur_st) if cur_st in st_list else 0, key=f"s_{item['id']}_{idx}")
                if nst != cur_st:
                    s["status"] = nst
                    s["updated_at"] = today
                    up_flag = True
                
                try: d_obj = datetime.datetime.strptime(s.get("deadline_date", def_dl), "%Y-%m-%d").date()
                except: d_obj = datetime.date.today()
                nd = cd.date_input("締切日", value=d_obj, key=f"dt_{item['id']}_{idx}")
                nd_str = nd.strftime("%Y-%m-%d")
                if nd_str != s.get("deadline_date"):
                    s["deadline_date"] = nd_str
                    s["updated_at"] = today
                    up_flag = True
                
                st.link_button("👉 受付ページへ飛ぶ", s.get("url", "https://google.com"), use_container_width=True)
                
        if up_flag:
            update_db(item["id"], {"sites": sites, "updated_at": today})
            st.rerun()
