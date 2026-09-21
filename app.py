import streamlit as st, datetime as dt, urllib.parse as up, json, time, feedparser, re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from supabase import create_client

st.set_page_config(page_title="プレ値パトロール", page_icon="🎯", layout="wide")

gk = st.secrets.get("GEMINI_API_KEY", "")
su, sk = st.secrets.get("SUPABASE_URL", ""), st.secrets.get("SUPABASE_KEY", "")
sb = create_client(su, sk) if su and sk else None

if "items_list" not in st.session_state: st.session_state.items_list = []

ICONS = {"TCG":"🃏","プレバン":"🤖","スニーカー":"👟","ホビー":"🧸","ソフビ":"🧸","釣具":"🎣","海外相場":"🌎","カメラ":"📷","キャンプ":"⛺"}

def get_icon(g):
    for k, v in ICONS.items():
        if k in str(g): return v
    return "📦"

def parse_num(v, d):
    c = re.sub(r"[^\d]", "", str(v)) if v else ""
    return int(c) if c else d

def parse_date_safe(val):
    if not val: return None
    try: return dt.datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except: return None

def load_db():
    if sb:
        try:
            res = sb.table("items").select("*").order("id", desc=True).execute().data
            if isinstance(res, list): return res
        except: pass
    return st.session_state.items_list

def save_db(it):
    if sb:
        try:
            sb.table("items").insert(it).execute()
            return
        except: pass
    st.session_state.items_list = [it] + [x for x in st.session_state.items_list if x.get("id") != it.get("id")]

def update_db(i_id, data):
    if sb:
        try: sb.table("items").update(data).eq("id", i_id).execute()
        except: pass

def del_db(i_id):
    if sb:
        try: sb.table("items").delete().eq("id", i_id).execute()
        except: pass
    st.session_state.items_list = [x for x in st.session_state.items_list if str(x.get("id")) != str(i_id)]

def load_x_accounts():
    if sb:
        try:
            res = sb.table("items").select("name").eq("url", "config:x_account").execute().data
            if res: return [x["name"].replace("@", "").strip() for x in res]
        except: pass
    return st.session_state.get("x_accounts", [])

def add_x_account(acc):
    h = acc.replace("@", "").strip()
    if not h: return
    if sb:
        try:
            sb.table("items").insert({"id": f"x_acc_{h}", "name": h, "url": "config:x_account", "sns_genre": "X監視"}).execute()
            return
        except: pass
    if "x_accounts" not in st.session_state: st.session_state.x_accounts = []
    if h not in st.session_state.x_accounts: st.session_state.x_accounts.append(h)

def del_x_account(acc):
    h = acc.replace("@", "").strip()
    if sb:
        try:
            sb.table("items").delete().eq("id", f"x_acc_{h}").execute()
            return
        except: pass
    if "x_accounts" in st.session_state and h in st.session_state.x_accounts:
        st.session_state.x_accounts.remove(h)

def fetch_web_text(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as response:
            html = response.read().decode('utf-8', errors='ignore')
            text = re.sub(r'<[^>]+>', ' ', html)
            return ' '.join(text.split())[:1500]
    except:
        return ""

def get_links(n, g):
    e, t = up.quote(n), str(g) + str(n)
    if any(k in t for k in ["TCG", "ポケカ", "ワンピ", "ドラゴンボール"]):
        res = [{"site_name": "あみあみ（予約抽選）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={e}", "deadline_date": None},
               {"site_name": "ヨドバシ", "url": f"https://www.yodobashi.com/?word={e}", "deadline_date": None},
               {"site_name": "スニダン相場", "url": f"https://snkrdunk.com/search?keywords={e}", "deadline_date": None}]
        if "ポケ" in t: res.insert(0, {"site_name": "ポケセン", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={e}", "deadline_date": None})
        elif "ドラゴンボール" in t: res.insert(0, {"site_name": "プレバン公式", "url": f"https://p-bandai.jp/chara/c0005/?keyword={e}", "deadline_date": None})
        return res
    if "プレバン" in t: return [{"site_name": "プレバン公式", "url": f"https://p-bandai.jp/chara/c0001/?keyword={e}", "deadline_date": None}]
    if any(k in t for k in ["スニーカー", "NIKE", "SNKRS"]): return [{"site_name": "SNKRS", "url": f"https://www.nike.com/jp/w?q={e}", "deadline_date": None}]
    if any(k in t for k in ["釣具", "DRT", "クラッシュ"]): return [{"site_name": "バックラッシュ", "url": f"https://www.backlash.co.jp/item_list/?kw={e}", "deadline_date": None}]
    return [{"site_name": "公式情報元", "url": "https://google.com", "deadline_date": None}]

def call_gemini(n, u, g, r):
    if not gk or any(bw in n or bw in r for bw in ["車", "自動車", "バイク", "タイヤ"]): return None
    body_txt = fetch_web_text(u) if u.startswith("http") else ""
    ctx = f"{r} {body_txt}"[:1800]
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。限定品アナリストとして定価,相場,受付締切日(YYYY-MM-DD),信頼度(0-100),理由をJSON出力。締切日が本文から正確に分からない場合はnullにすること。対象:{n},{u},{g},{ctx}。形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"{g}\",\"trust_score\":85,\"trust_reason\":\"本文確認\",\"sites\":[{{\"site_name\":\"受付元\",\"url\":\"{u}\",\"deadline\":null}}]}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def call_gemini_tweet_parse(tweet_text):
    if not gk: return None
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。以下のXポストから限定品情報を解析しJSON出力せよ。商品名、定価、予想相場、受付締切日(YYYY-MM-DD)、リンクURL、ジャンル(TCG/プレバン/スニーカー/ホビー/ソフビ/釣具/海外相場/カメラ/キャンプ/その他)。不明な締切やURLはnull。対象文:\n{tweet_text}\n形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"TCG\",\"url\":\"URLまたはnull\",\"trust_score\":95,\"trust_reason\":\"X速報解析\"}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def fetch_rss(accounts):
    hits = []
    qs = [("ポケカ 抽選予約 予約開始","TCG"),("ワンピースカード 抽選予約","TCG"),("プレミアムバンダイ 受注開始 限定","プレバン"),("Nike SNKRS 抽選","スニーカー"),("ジャンプキャラクターズストア 受注","ホビー"),("TS-NEO ソフビ 抽選","ソフビ"),("当時物 ソフビ 落札","ソフビ"),("site:ameblo.jp タイニークラッシュ 抽選","釣具"),("DRT タイニークラッシュ 抽選","釣具"),("ドラゴンボール カード 鑑定 PSA 落札","海外相場"),("海外相場 高騰 オークション","海外相場"),("ガレージブランド キャンプ 抽選","キャンプ")]
    for q, g in qs:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={up.quote(q)}&hl=ja&gl=JP&ceid=JP:ja")
        for e in f.entries[:2]: hits.append({"name": e.title, "url": e.link, "genre": g, "summary": getattr(e, "summary", "")})
    for handle in accounts:
        if not handle: continue
        for inst in ["https://nitter.net", "https://nitter.cz"]:
            try:
                xf = feedparser.parse(f"{inst}/{handle}/rss")
                for xe in xf.entries[:3]:
                    hits.append({"name": f"【X:@{handle}】{xe.title[:30]}", "url": xe.link, "genre": "その他", "summary": getattr(xe, "summary", "")})
                if xf.entries: break
            except: pass
    return hits

st.subheader("🎯 プレ値パトロール")
today_d = dt.date.today()
today = today_d.strftime("%Y-%m-%d")

items = [x for x in load_db() if x.get("url") != "config:x_account"]
norm_items = []
for it in items:
    s = it.get("sites")
    if isinstance(s, str):
        try: s = json.loads(s)
        except: s = []
    if not s:
        s = [{"site_name": "情報元", "url": it.get("url","https://google.com"), "created_at": today, "updated_at": today, "deadline_date": None, "status": "未応募"}]
        for l in get_links(it.get("name",""), it.get("sns_genre","")): s.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
        update_db(it["id"], {"sites": s})
    it["sites"] = s
    norm_items.append(it)

tab_auto, tab_manual = st.tabs(["🎯 自動巡回リスト", "⚡ Xコピペ追加・監視設定"])

def render_item_list(item_list, key_prefix=""):
    for item in item_list:
        sites = item.get("sites", [])
        has_app = any(s.get("status") == "応募中" for s in sites)
        pv = item.get('profit', 0)
        p_badge = f"{'🔥' if pv >= 50000 else '💰'} +{pv:,}円" if pv >= 10000 else ""
        with st.expander(f"{get_icon(item.get('sns_genre',''))} {item.get('name','')} {p_badge} {'【応募中】' if has_app else ''}"):
            ci, cd = st.columns([5, 1])
            ci.caption(f"🛡️ 信頼度: {item.get('trust_score',70)}点（{item.get('trust_reason','通常')}） | 更新: {item.get('updated_at','-')}")
            if cd.button("削除", key=f"del_{key_prefix}_{item['id']}"):
                del_db(item["id"])
                st.rerun()
            c_m1, c_m2 = st.columns(2)
            c_m1.metric("定価", f"¥{item.get('retail_price',0):,}")
            c_m2.metric("相場", f"¥{item.get('market_price',0):,}")
            c_m3, c_m4 = st.columns(2)
            c_m3.metric("利益", f"¥{pv:,}", f"{item.get('margin_rate',0)}%")
            c_m4.metric("損益分岐", f"¥{item.get('break_even',0):,}")
            cx1, cx2 = st.columns([1, 1])
            with cx1:
                with st.popover("𝕏 ポスト文面", use_container_width=True):
                    stars = "★★★★★" if pv >= 30000 else "★★★★☆" if pv >= 10000 else "★★★☆☆"
                    dl_found = [s.get("deadline_date") for s in sites if s.get("deadline_date")]
                    dl_str = min(dl_found) if dl_found else "公式アナウンス確認推奨"
                    tw_main = f"【定価購入アラート🚨】\n二次流通でのプレ値高騰が予想される注目アイテムです。定価で手に入れたい方は公式受付をお見逃しなく！\n\n📦 {item.get('name','')}\n・定価目安: ¥{item.get('retail_price',0):,}\n・注目度: {stars}（市場目安: 約¥{item.get('market_price',0):,}〜）\n\n⏰ 締切目安: {dl_str}\n⚠️ 忘れ防止に【ブックマーク🔖】推奨\n\n👇 応募先リンクはリプライ欄に記載\n#{item.get('sns_genre','限定品')} #定価購入 #抽選速報"
                    tw_rep = "【受付リンク】\n" + "\n".join([f"・{s.get('site_name')}: {s.get('url')}" for s in sites[:2]])
                    st.text_area("本文", tw_main, height=120, key=f"tw_m_{key_prefix}_{item['id']}")
                    st.link_button("👉 𝕏 投稿画面へ", f"https://twitter.com/intent/tweet?text={up.quote(tw_main)}", use_container_width=True)
                    st.text_area("リプライ用", tw_rep, height=70, key=f"tw_r_{key_prefix}_{item['id']}")
            with cx2:
                if st.button("🔄 相場・締切再取得", key=f"r_{key_prefix}_{item['id']}", use_container_width=True):
                    d = call_gemini(item.get("name",""), item.get("url",""), item.get("sns_genre",""), "")
                    if d and isinstance(d, dict):
                        rp, mp = parse_num(d.get("retail_price"), item.get("retail_price",5000)), parse_num(d.get("market_price"), item.get("market_price",15000))
                        new_dl = d.get("deadline")
                        for s in sites:
                            if new_dl and not s.get("deadline_date"): s["deadline_date"] = new_dl
                        update_db(item["id"], {"retail_price": rp, "market_price": mp, "profit": mp - int(mp * 0.1) - 750 - rp, "sites": sites, "trust_score": parse_num(d.get("trust_score"), 70), "trust_reason": d.get("trust_reason") or "再取得", "updated_at": today})
                        st.rerun()

            kw = up.quote(item.get("name",""))
            k1, k2, k3 = st.columns(3)
            k1.link_button("👟 スニダン", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
            k2.link_button("🔴 メルカリ", f"https://jp.mercari.com/search?keyword={kw}&status=sold_out", use_container_width=True)
            k3.link_button("🇺🇸 eBay", f"https://www.ebay.com/sch/i.html?_nkw={kw}&LH_Complete=1&LH_Sold=1", use_container_width=True)

            for idx, s in enumerate(sites):
                with st.container(border=True):
                    cur_dl_txt = s.get("deadline_date") or "未設定"
                    st.write(f"🔗 **{s.get('site_name')}** (締切: `{cur_dl_txt}`)")
                    cs_st, cs_dt = st.columns(2)
                    cur_st = s.get("status", "未応募")
                    nst = cs_st.selectbox("状況", ["未応募", "応募中", "当選", "落選"], index=["未応募", "応募中", "当選", "落選"].index(cur_st) if cur_st in ["未応募", "応募中", "当選", "落選"] else 0, key=f"s_{key_prefix}_{item['id']}_{idx}")
                    d_val = parse_date_safe(s.get("deadline_date")) or today_d
                    nd = cs_dt.date_input("締切編集", value=d_val, key=f"dt_{key_prefix}_{item['id']}_{idx}").strftime("%Y-%m-%d")
                    if nst != cur_st or (s.get("deadline_date") and nd != s.get("deadline_date")):
                        s["status"], s["deadline_date"], s["updated_at"] = nst, nd, today
                        update_db(item["id"], {"sites": sites, "updated_at": today})
                        st.rerun()
                    st.link_button("👉 サイトを開く", s.get("url", "https://google.com"), use_container_width=True)

x_acc_list = load_x_accounts()

with tab_auto:
    if st.button("🔄 最新情報を高速巡回（登録済み全商品を追跡）", use_container_width=True):
        pt, pb = st.empty(), st.progress(0)
        hits, all_it = fetch_rss(x_acc_list), load_db()
        c_map = {x.get("url"): parse_date_safe(x.get("updated_at") or x.get("created_at")) or (today_d - dt.timedelta(days=10)) for x in all_it if x.get("url")}
        new_h = [h for h in hits if h["url"] not in c_map or (today_d - c_map[h["url"]]).days >= 3]
        total, done = len(new_h) or 1, 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_map = {ex.submit(call_gemini, h["name"], h["url"], h["genre"], h["summary"]): h for h in new_h}
            for fut in as_completed(f_map):
                done += 1
                pt.markdown(f"**高速巡回中... {int(done/total*100)}%**")
                pb.progress(done / total)
                h, d = f_map[fut], fut.result()
                if d and isinstance(d, dict):
                    pn = d.get("standard_name") or h["name"][:25]
                    rp, mp = parse_num(d.get("retail_price"), 5000), parse_num(d.get("market_price"), 15000)
                    pf = mp - int(mp * 0.1) - 750 - rp
                    mr = round((pf / mp) * 100, 1) if mp > 0 else 0
                    fg = d.get("genre") or h["genre"]
                    ts, tr = parse_num(d.get("trust_score"), 70), d.get("trust_reason") or "本文確認"
                    raw_dl = d.get("deadline")
                    sites = [{**s, "created_at": today, "updated_at": today, "deadline_date": s.get("deadline") or raw_dl, "status": "未応募"} for s in d.get("sites", [])]
                    for l in get_links(pn, fg):
                        if not any(x["site_name"] == l["site_name"] for x in sites): sites.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
                    match = next((x for x in all_it if pn in x.get("name","") or x.get("url") == h["url"]), None)
                    if match:
                        update_db(match["id"], {"retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9), "trust_score": ts, "trust_reason": tr, "updated_at": today})
                    else:
                        save_db({"id": str(int(time.time()*1000)+done), "name": pn, "url": h["url"], "retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9), "sns_genre": fg, "trust_score": ts, "trust_reason": tr, "is_manual": False, "created_at": today, "updated_at": today, "sites": sites})
        pt.empty(); pb.empty()
        st.success("完了！"); st.rerun()

    # 自動巡回リスト（Xコピペから追加されたものも含む）
    st.caption(f"📦 巡回リスト: **{len(norm_items)}** 件")
    render_item_list(norm_items, "auto")

with tab_manual:
    with st.container(border=True):
        st.write("⚡ **XポストをAI解析 ➔ 『自動巡回リスト』に直接追加**")
        with st.form("x_parse_form", clear_on_submit=True):
            tw_input = st.text_area("Xの投稿文（テキスト全体）をペースト", placeholder="【抽選開始】ポケモンカード最新弾『〇〇』受付開始！定価5,400円、締切は9月25日まで。URL: https://...", height=90)
            submitted = st.form_submit_button("🚀 AI解析して自動巡回リストに追加", use_container_width=True)
            if submitted and tw_input.strip():
                with st.spinner("AIが解析して巡回リストに登録中..."):
                    parsed = call_gemini_tweet_parse(tw_input)
                    if parsed and isinstance(parsed, dict):
                        p_name = parsed.get("standard_name") or "X解析アイテム"
                        p_url = parsed.get("url") or "https://x.com"
                        p_genre = parsed.get("genre") or "その他"
                        p_rp = parse_num(parsed.get("retail_price"), 5000)
                        p_mp = parse_num(parsed.get("market_price"), 15000)
                        p_pf = p_mp - int(p_mp * 0.1) - 750 - p_rp
                        p_mr = round((p_pf / p_mp) * 100, 1) if p_mp > 0 else 0
                        p_dl = parsed.get("deadline")
                        sites = [{"site_name": "X告知元/受付", "url": p_url, "created_at": today, "updated_at": today, "deadline_date": p_dl, "status": "未応募"}]
                        for l in get_links(p_name, p_genre): sites.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
                        
                        # 自動巡回リスト（norm_items）に直接合流させる
                        save_db({
                            "id": str(int(time.time()*1000)),
                            "name": p_name,
                            "url": p_url,
                            "retail_price": p_rp,
                            "market_price": p_mp,
                            "profit": p_pf,
                            "margin_rate": p_mr,
                            "break_even": int((p_rp+750)/0.9),
                            "sns_genre": p_genre,
                            "trust_score": 95,
                            "trust_reason": "X速報(巡回対象)",
                            "is_manual": False,
                            "created_at": today,
                            "updated_at": today,
                            "sites": sites
                        })
                        st.toast(f"✅ 自動巡回リストに追加完了: {p_name}")
                        time.sleep(0.3)
                        st.rerun()

    with st.expander(f"👀 巡回監視アカウント設定（登録中: {len(x_acc_list)}件）"):
        col_u1, col_u2 = st.columns([3, 1])
        new_acc = col_u1.text_input("アカウントID（例: pokecainfo）", key="acc_in")
        if col_u2.button("登録", use_container_width=True) and new_acc:
            add_x_account(new_acc)
            st.rerun()
        if x_acc_list:
            for acc in x_acc_list:
                c_a, c_b = st.columns([4, 1])
                c_a.write(f"・ `@{acc}`")
                if c_b.button("解除", key=f"del_acc_{acc}"):
                    del_x_account(acc)
                    st.rerun()
