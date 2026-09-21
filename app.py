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

ICONS = {"TCG":"🃏","プレバン":"🤖","スニーカー":"👟","コフレ":"💄","コスメ":"💄","ホビー":"🧸","ソフビ":"🧸","釣具":"🎣","海外相場":"🌎","カメラ":"📷","キャンプ":"⛺"}

def get_icon(g):
    for k, v in ICONS.items():
        if k in str(g): return v
    return "📦"

def parse_num(v, d):
    if v is None: return d
    c = re.sub(r"[^\d\-]", "", str(v))
    try: return int(c)
    except: return d

def parse_date_safe(val):
    if not val: return None
    try: return dt.datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except: return None

def clean_url(raw_text):
    if not raw_text: return ""
    m = re.search(r'https?://[^\s)\]"]+', str(raw_text))
    return m.group(0) if m else raw_text.strip()

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

def load_custom_rss():
    if sb:
        try:
            res = sb.table("items").select("id, name, url").eq("sns_genre", "カスタムRSS").execute().data
            if res: return res
        except: pass
    return st.session_state.get("custom_rss", [])

def add_custom_rss(name, raw_input):
    target_url = clean_url(raw_input)
    if not target_url or not target_url.startswith("http"): return
    
    if name and name.strip():
        final_name = name.strip()[:20]
    else:
        # 名前未入力時はドメインやタイトルから短くスマートに命名
        domain = up.urlparse(target_url).netloc.replace("www.", "")
        final_name = "丸井 / パルワールド" if "0101" in target_url or "marui" in target_url or "AKhZ" in target_url else ("眼鏡市場 遊戯王" if "megane" in target_url else domain[:15] or "特設サイト")

    it = {"id": f"rss_{int(time.time()*1000)}", "name": final_name, "url": target_url, "sns_genre": "カスタムRSS"}
    if sb:
        try:
            sb.table("items").insert(it).execute()
            return
        except: pass
    if "custom_rss" not in st.session_state: st.session_state.custom_rss = []
    st.session_state.custom_rss.append(it)

def del_custom_rss(i_id):
    if sb:
        try:
            sb.table("items").delete().eq("id", i_id).execute()
            return
        except: pass
    if "custom_rss" in st.session_state:
        st.session_state.custom_rss = [x for x in st.session_state.custom_rss if x.get("id") != i_id]

def fetch_web_text(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8', errors='ignore')
            text = re.sub(r'<[^>]+>', ' ', html)
            return ' '.join(text.split())[:1800]
    except:
        return ""

def get_links(n, g):
    e, t = up.quote(n), str(g) + str(n)
    if any(k in t for k in ["コフレ", "コスメ", "ホリデー"]):
        return [
            {"site_name": "meeco(三越伊勢丹)", "url": f"https://meeco.mistore.jp/meeco/search?q={e}", "deadline_date": None},
            {"site_name": "阪急うめだコスメ", "url": f"https://web.hh-online.jp/hankyu-beauty/goods/list.html?shop=hb&keyword={e}", "deadline_date": None},
            {"site_name": "アットコスメ", "url": f"https://www.cosme.com/products/list.php?name={e}", "deadline_date": None}
        ]
    if any(k in t for k in ["TCG", "ポケカ", "ワンピ", "ドラゴンボール", "遊戯王"]):
        res = [{"site_name": "あみあみ（予約抽選）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={e}", "deadline_date": None},
               {"site_name": "ヨドバシ", "url": f"https://www.yodobashi.com/?word={e}", "deadline_date": None},
               {"site_name": "スニダン相場", "url": f"https://snkrdunk.com/search?keywords={e}", "deadline_date": None}]
        if "ポケ" in t: res.insert(0, {"site_name": "ポケセン", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={e}", "deadline_date": None})
        elif "ドラゴンボール" in t or "プレバン" in t: res.insert(0, {"site_name": "プレバン公式", "url": f"https://p-bandai.jp/chara/c0005/?keyword={e}", "deadline_date": None})
        return res
    if "プレバン" in t: return [{"site_name": "プレバン公式", "url": f"https://p-bandai.jp/chara/c0001/?keyword={e}", "deadline_date": None}]
    if any(k in t for k in ["スニーカー", "NIKE", "SNKRS"]): return [{"site_name": "SNKRS", "url": f"https://www.nike.com/jp/w?q={e}", "deadline_date": None}]
    if any(k in t for k in ["釣具", "DRT", "クラッシュ"]): return [{"site_name": "バックラッシュ", "url": f"https://www.backlash.co.jp/item_list/?kw={e}", "deadline_date": None}]
    return [{"site_name": "公式情報元", "url": "https://google.com", "deadline_date": None}]

def call_gemini(n, u, g, r):
    if not gk: return None
    body_txt = fetch_web_text(u) if u.startswith("http") else ""
    ctx = f"{r} {body_txt}"[:2000]
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。限定品アナリストとして定価,予想相場,受付締切日(YYYY-MM-DD),信頼度(0-100),理由をJSON出力。締切日が本文から正確に分からない場合はnull。対象:{n},{u},{g},{ctx}。形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"{g}\",\"trust_score\":85,\"trust_reason\":\"本文確認\",\"sites\":[{{\"site_name\":\"受付元\",\"url\":\"{u}\",\"deadline\":null}}]}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def call_gemini_tweet_parse(tweet_text):
    if not gk: return None
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。以下のXポストや告知文から限定品情報を解析しJSON出力せよ。商品名、定価、予想相場、受付締切日(YYYY-MM-DD),リンクURL,ジャンル(TCG/プレバン/スニーカー/コフレ/ホビー/ソフビ/釣具/海外相場/カメラ/キャンプ/その他)。不明な締切やURLはnull。対象文:\n{tweet_text}\n形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"ホビー\",\"url\":\"URLまたはnull\",\"trust_score\":95,\"trust_reason\":\"AI抽出\"}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def fetch_all_feeds(custom_list):
    hits = []
    qs = [
        ("クリスマスコフレ 予約 抽選","コフレ"),("コスメデコルテ コフレ 予約 抽選","コフレ"),("Dior ホリデー 限定 予約","コフレ"),
        ("ポケカ 抽選予約 予約開始","TCG"),("ワンピースカード 抽選予約","TCG"),("プレミアムバンダイ 受注開始 限定","プレバン"),
        ("Nike SNKRS 抽選","スニーカー"),("ジャンプキャラクターズストア 受注","ホビー"),("TS-NEO ソフビ 抽選","ソフビ"),
        ("当時物 ソフビ 落札","ソフビ"),("site:ameblo.jp タイニークラッシュ 抽選","釣具"),("DRT タイニークラッシュ 抽選","釣具"),
        ("ドラゴンボール カード 鑑定 PSA 落札","海外相場"),("海外相場 高騰 オークション","海外相場"),("ガレージブランド キャンプ 抽選","キャンプ")
    ]
    for q, g in qs:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={up.quote(q)}&hl=ja&gl=JP&ceid=JP:ja")
        for e in f.entries[:2]: hits.append({"name": e.title, "url": e.link, "genre": g, "summary": getattr(e, "summary", "")})
    
    for cr in custom_list:
        u = clean_url(cr.get("url", ""))
        if not u or not u.startswith("http"): continue
        cf = feedparser.parse(u)
        if cf.entries:
            for ce in cf.entries[:3]:
                hits.append({"name": f"【{cr['name']}】{ce.title}", "url": ce.link, "genre": "ホビー", "summary": getattr(ce, "summary", "")})
        else:
            hits.append({"name": f"【特設】{cr['name']}", "url": u, "genre": "ホビー", "summary": "特設ページ直接解析"})
    return hits

st.subheader("🎯 プレ値パトロール")
today_d = dt.date.today()
today = today_d.strftime("%Y-%m-%d")

custom_feeds = load_custom_rss()

with st.container(border=True):
    st.write("⚡ **Xポスト・告知文・特設LPをAI解析して巡回追加**")
    with st.form("x_parse_form", clear_on_submit=True):
        tw_input = st.text_area("X投稿や告知文、または特設ページの紹介文を貼り付け", placeholder="例：【予約開始】パルワールドPOP UP限定グッズ発売！価格3,500円〜、受注受付中。URL: https://...", height=80)
        if st.form_submit_button("🚀 AI解析してリストに追加", use_container_width=True) and tw_input.strip():
            with st.spinner("AIが解析中..."):
                parsed = call_gemini_tweet_parse(tw_input)
                if parsed and isinstance(parsed, dict):
                    p_name = parsed.get("standard_name") or "解析アイテム"
                    p_url = clean_url(parsed.get("url")) or "https://google.com"
                    p_genre = parsed.get("genre") or "その他"
                    p_rp = parse_num(parsed.get("retail_price"), 5000)
                    p_mp = parse_num(parsed.get("market_price"), 15000)
                    p_pf = p_mp - int(p_mp * 0.1) - 750 - p_rp
                    p_mr = round((p_pf / p_mp) * 100, 1) if p_mp > 0 else 0
                    p_dl = parsed.get("deadline")
                    sites = [{"site_name": "告知・受付元", "url": p_url, "created_at": today, "updated_at": today, "deadline_date": p_dl, "status": "未応募"}]
                    for l in get_links(p_name, p_genre): sites.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
                    save_db({"id": str(int(time.time()*1000)), "name": p_name, "url": p_url, "retail_price": p_rp, "market_price": p_mp, "profit": p_pf, "margin_rate": p_mr, "break_even": int((p_rp+750)/0.9), "sns_genre": p_genre, "trust_score": 95, "trust_reason": "告知AI抽出", "created_at": today, "updated_at": today, "sites": sites})
                    st.toast(f"✅ 追加完了: {p_name}")
                    time.sleep(0.3)
                    st.rerun()

c_btn1, c_btn2 = st.columns([2, 1])
with c_btn1:
    btn_run = st.button("🔄 最新情報を高速巡回（特設サイト巡回含む）", use_container_width=True)
with c_btn2:
    with st.popover("➕ 特設・速報サイト登録"):
        st.caption("特設サイトURLやブログRSSを登録")
        r_name = st.text_input("サイト名 (例: パルワールド 渋谷)")
        r_url = st.text_input("URL (特設LPまたはRSS)")
        if st.button("登録する", use_container_width=True) and r_url:
            add_custom_rss(r_name, r_url)
            st.rerun()
        if custom_feeds:
            st.markdown("---")
            st.caption("登録中サイト一覧:")
            for cf_item in custom_feeds:
                cc1, cc2 = st.columns([3, 1])
                target_url = clean_url(cf_item.get('url', ''))
                # タイトルを最大16文字にトリミングし、安全なリンクボタンで表示
                raw_title = cf_item.get('name') or "特設サイト"
                disp_title = (raw_title[:15] + "…") if len(raw_title) > 15 else raw_title
                if target_url.startswith("http"):
                    cc1.link_button(f"👉 {disp_title}", target_url, use_container_width=True)
                else:
                    cc1.write(f"・{disp_title}")
                if cc2.button("削除", key=f"del_rss_{cf_item['id']}"):
                    del_custom_rss(cf_item["id"])
                    st.rerun()

all_raw_items = load_db()
items = [x for x in all_raw_items if x.get("sns_genre") != "カスタムRSS"]
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

if btn_run:
    pt, pb = st.empty(), st.progress(0)
    hits = fetch_all_feeds(custom_feeds)
    c_map = {clean_url(x.get("url")): parse_date_safe(x.get("updated_at") or x.get("created_at")) or (today_d - dt.timedelta(days=10)) for x in items if x.get("url")}
    new_h = [h for h in hits if clean_url(h["url"]) not in c_map or (today_d - c_map[clean_url(h["url"])]).days >= 3]
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
                match = next((x for x in items if pn in x.get("name","") or clean_url(x.get("url")) == clean_url(h["url"])), None)
                if match:
                    update_db(match["id"], {"retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9), "trust_score": ts, "trust_reason": tr, "updated_at": today})
                else:
                    save_db({"id": str(int(time.time()*1000)+done), "name": pn, "url": clean_url(h["url"]), "retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9), "sns_genre": fg, "trust_score": ts, "trust_reason": tr, "created_at": today, "updated_at": today, "sites": sites})
    pt.empty(); pb.empty()
    st.success("巡回完了！"); st.rerun()

st.markdown("---")
cf, cs = st.columns([1, 1])
with cf:
    s_gen = st.selectbox("ジャンル", ["すべて", "💄 コフレ", "🃏 TCG", "🤖 プレバン", "👟 スニーカー", "🧸 ホビー/ソフビ", "🎣 釣具", "🌎 海外相場", "📷 カメラ", "⛺ キャンプ"])
    f_app = st.checkbox(f"【応募中】のみ表示（{sum(1 for x in norm_items if any(s.get('status')=='応募中' for s in x.get('sites',[])))}件）")
with cs:
    s_mode = st.selectbox("並び順", ["更新順", "新着順", "利益額順", "利益率順", "相場順", "締切順"])

fil_items = [it for it in norm_items if (s_gen == "すべて" or (s_gen.split()[-1] in it.get("sns_genre",""))) and (not f_app or any(s.get("status") == "応募中" for s in it.get("sites",[])))]
if s_mode == "利益額順": fil_items.sort(key=lambda x: parse_num(x.get("profit"), 0), reverse=True)
elif s_mode == "利益率順": fil_items.sort(key=lambda x: float(x.get("margin_rate") or 0), reverse=True)
elif s_mode == "相場順": fil_items.sort(key=lambda x: parse_num(x.get("market_price"), 0), reverse=True)
elif s_mode == "締切順": fil_items.sort(key=lambda x: min([s.get("deadline_date") for s in x.get("sites",[]) if s.get("deadline_date")] or ["9999-99-99"]))
elif s_mode == "新着順": fil_items.sort(key=lambda x: str(x.get("id","")), reverse=True)
else: fil_items.sort(key=lambda x: str(x.get("updated_at","")), reverse=True)

st.caption(f"📦 パトロール対象: **{len(fil_items)}** 件")

for item in fil_items:
    sites = item.get("sites", [])
    has_app = any(s.get("status") == "応募中" for s in sites)
    pv = parse_num(item.get('profit'), 0)
    p_badge = f"{'🔥' if pv >= 50000 else '💰'} +{pv:,}円" if pv >= 10000 else ""
    with st.expander(f"{get_icon(item.get('sns_genre',''))} {item.get('name','')} {p_badge} {'【応募中】' if has_app else ''}"):
        ci, cd = st.columns([5, 1])
        ci.caption(f"🛡️ 信頼度: {item.get('trust_score',70)}点（{item.get('trust_reason','通常')}） | 更新: {item.get('updated_at','-')}")
        if cd.button("削除", key=f"del_{item['id']}"): del_db(item["id"]); st.rerun()
        
        c_m1, c_m2 = st.columns(2)
        c_m1.metric("定価", f"¥{parse_num(item.get('retail_price'), 0):,}")
        c_m2.metric("相場", f"¥{parse_num(item.get('market_price'), 0):,}")
        c_m3, c_m4 = st.columns(2)
        c_m3.metric("利益", f"¥{pv:,}", f"{item.get('margin_rate',0)}%")
        c_m4.metric("損益分岐", f"¥{parse_num(item.get('break_even'), 0):,}")
        
        cx1, cx2 = st.columns([1, 1])
        with cx1:
            with st.popover("𝕏 ポスト文面", use_container_width=True):
                stars = "★★★★★" if pv >= 30000 else "★★★★☆" if pv >= 10000 else "★★★☆☆"
                dl_found = [s.get("deadline_date") for s in sites if s.get("deadline_date")]
                dl_str = min(dl_found) if dl_found else "公式アナウンス確認推奨"
                tw_main = f"【定価購入アラート🚨】\n二次流通でのプレ値高騰が予想される注目アイテムです。定価で手に入れたい方は公式受付をお見逃しなく！\n\n📦 {item.get('name','')}\n・定価目安: ¥{parse_num(item.get('retail_price'), 0):,}\n・注目度: {stars}（市場目安: 約¥{parse_num(item.get('market_price'), 0):,}〜）\n\n⏰ 締切目安: {dl_str}\n⚠️ 忘れ防止に【ブックマーク🔖】推奨\n\n👇 応募先リンクはリプライ欄に記載\n#{item.get('sns_genre','限定品')} #定価購入 #抽選速報"
                tw_rep = "【受付リンク】\n" + "\n".join([f"・{s.get('site_name')}: {s.get('url')}" for s in sites[:2]])
                st.text_area("本文", tw_main, height=120, key=f"tw_m_{item['id']}")
                st.link_button("👉 𝕏 投稿画面へ", f"https://twitter.com/intent/tweet?text={up.quote(tw_main)}", use_container_width=True)
                st.text_area("リプライ用", tw_rep, height=70, key=f"tw_r_{item['id']}")
        with cx2:
            if st.button("🔄 相場・締切再取得", key=f"r_{item['id']}", use_container_width=True):
                d = call_gemini(item.get("name",""), clean_url(item.get("url","")), item.get("sns_genre",""), "")
                if d and isinstance(d, dict):
                    rp, mp = parse_num(d.get("retail_price"), parse_num(item.get('retail_price'), 5000)), parse_num(d.get("market_price"), parse_num(item.get('market_price'), 15000))
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
                nst = cs_st.selectbox("状況", ["未応募", "応募中", "当選", "落選"], index=["未応募", "応募中", "当選", "落選"].index(cur_st) if cur_st in ["未応募", "応募中", "当選"
