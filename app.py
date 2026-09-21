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

ICONS = {"TCG":"🃏","プレバン":"🤖","スニーカー":"👟","コフレ":"💄","ホビー":"🧸","ソフビ":"🧸","釣具":"🎣","海外相場":"🌎","カメラ":"📷","キャンプ":"⛺"}
STATUS_OPTS = ["未応募", "応募中", "当選", "落選"]

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
    return m.group(0) if m else str(raw_text).strip()

def get_smart_name(raw_name, raw_url):
    u = clean_url(raw_url).lower()
    if raw_name and raw_name not in ["速報サイト", "特設サイト", ""]: return raw_name[:14]
    if "megane" in u or "yugio" in u: return "眼鏡市場×遊戯王"
    if "0101" in u or "marui" in u or "palworld" in u or "akhz" in u: return "丸井パルワールド"
    if "bandai" in u: return "プレバン特設"
    d = up.urlparse(u).netloc.replace("www.", "")
    return d[:14] if d else "特設サイト"

def get_target_hook(name, genre):
    t = f"{name} {genre}".lower()
    if "遊戯王" in t: return "【遊戯王ファン必見🚨】"
    if "ポケ" in t: return "【ポケカ速報🚨】"
    if "ワンピ" in t: return "【ワンピカード速報🚨】"
    if any(k in t for k in ["コフレ", "コスメ", "ホリデー"]): return "【争奪戦警報💄 デパコス速報】"
    if any(k in t for k in ["パルワールド", "palworld"]): return "【パルワールド注目速報📢】"
    if "スニーカー" in t or "snkrs" in t: return "【SNKRS抽選アラート👟】"
    if "プレバン" in t: return "【プレバン限定解禁🤖】"
    return "【定価購入アラート🚨】"

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
    final_name = get_smart_name(name, target_url)
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
    if any(k in t for k in ["コフレ", "コスメ"]):
        return [{"site_name": "meeco(伊勢丹)", "url": f"https://meeco.mistore.jp/meeco/search?q={e}", "deadline_date": None}]
    if any(k in t for k in ["TCG", "ポケカ", "遊戯王"]):
        return [{"site_name": "あみあみ予約", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={e}", "deadline_date": None},
                {"site_name": "スニダン相場", "url": f"https://snkrdunk.com/search?keywords={e}", "deadline_date": None}]
    return [{"site_name": "公式情報元", "url": "https://google.com", "deadline_date": None}]

def call_gemini(n, u, g, r):
    if not gk: return None
    body_txt = fetch_web_text(u) if u.startswith("http") else ""
    ctx = f"{r} {body_txt}"[:2000]
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。限定品アナリストとして商品名,定価(不明なら0),予想相場(不明なら0),受付締切(YYYY-MM-DD/不明ならnull),信頼度(0-100),理由をJSON出力。特設予告も抽出せよ。対象:{n},{u},{g},{ctx}。形式:{{\"standard_name\":\"商品名\",\"retail_price\":0,\"market_price\":0,\"deadline\":null,\"genre\":\"{g}\",\"is_teaser\":true,\"trust_score\":90,\"trust_reason\":\"特設解禁\",\"sites\":[{{\"site_name\":\"特設元\",\"url\":\"{u}\",\"deadline\":null}}]}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def call_gemini_tweet_parse(tweet_text):
    if not gk: return None
    try:
        td = dt.date.today().strftime("%Y-%m-%d")
        p = f"本日は{td}。告知文から限定品情報を解析しJSON出力。商品名,定価(不明なら0),相場(不明なら0),締切(YYYY-MM-DD/null),URL,ジャンル。文:\n{tweet_text}\n形式:{{\"standard_name\":\"商品名\",\"retail_price\":0,\"market_price\":0,\"deadline\":null,\"genre\":\"ホビー\",\"url\":\"URLまたはnull\",\"is_teaser\":false,\"trust_score\":95,\"trust_reason\":\"AI抽出\"}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("```","").strip())
    except: return None

def fetch_all_feeds(custom_list):
    hits = []
    qs = [("クリスマスコフレ 予約 抽選","コフレ"),("ポケカ 抽選予約 予約開始","TCG"),("ワンピースカード 抽選予約","TCG"),("プレミアムバンダイ 受注開始 限定","プレバン"),("Nike SNKRS 抽選","スニーカー"),("ジャンプキャラクターズストア 受注","ホビー"),("DRT タイニークラッシュ 抽選","釣具")]
    for q, g in qs:
        f = feedparser.parse(f"https://news.google.com/rss/search?q={up.quote(q)}&hl=ja&gl=JP&ceid=JP:ja")
        for e in f.entries[:2]: hits.append({"name": e.title, "url": e.link, "genre": g, "summary": getattr(e, "summary", "")})
    for cr in custom_list:
        u = clean_url(cr.get("url", ""))
        if not u or not u.startswith("http"): continue
        cf = feedparser.parse(u)
        name_label = get_smart_name(cr.get('name'), u)
        if cf.entries:
            for ce in cf.entries[:3]: hits.append({"name": f"【{name_label}】{ce.title}", "url": ce.link, "genre": "ホビー", "summary": getattr(ce, "summary", "")})
        else:
            hits.append({"name": f"【先行告知】{name_label}", "url": u, "genre": "ホビー", "summary": "特設ページ直接解析"})
    return hits

st.subheader("🎯 プレ値パトロール")
today_d = dt.date.today()
today = today_d.strftime("%Y-%m-%d")

custom_feeds = load_custom_rss()

with st.container(border=True):
    st.write("⚡ **Xポスト・告知文・特設LPをAI解析して巡回追加**")
    with st.form("x_parse_form", clear_on_submit=True):
        tw_input = st.text_area("X投稿や告知文、特設の紹介文を貼り付け", placeholder="【予約開始】限定グッズ発売！定価3,500円、締切〇月〇日。URL: https://...", height=80)
        if st.form_submit_button("🚀 AI解析してリストに追加", use_container_width=True) and tw_input.strip():
            with st.spinner("AIが解析中..."):
                parsed = call_gemini_tweet_parse(tw_input)
                if parsed and isinstance(parsed, dict):
                    p_name = parsed.get("standard_name") or "解析アイテム"
                    p_url = clean_url(parsed.get("url")) or "https://google.com"
                    p_genre = parsed.get("genre") or "その他"
                    p_rp, p_mp = parse_num(parsed.get("retail_price"), 0), parse_num(parsed.get("market_price"), 0)
                    p_pf = p_mp - int(p_mp * 0.1) - 750 - p_rp if (p_mp and p_rp) else 0
                    p_mr = round((p_pf / p_mp) * 100, 1) if p_mp > 0 else 0
                    p_dl = parsed.get("deadline")
                    sites = [{"site_name": "告知・受付元", "url": p_url, "created_at": today, "updated_at": today, "deadline_date": p_dl, "status": "未応募"}]
                    for l in get_links(p_name, p_genre): sites.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
                    save_db({"id": str(int(time.time()*1000)), "name": p_name, "url": p_url, "retail_price": p_rp, "market_price": p_mp, "profit": p_pf, "margin_rate": p_mr, "break_even": int((p_rp+750)/0.9) if p_rp else 0, "sns_genre": p_genre, "trust_score": 95, "trust_reason": "告知AI抽出", "is_teaser": parsed.get("is_teaser", False), "created_at": today, "updated_at": today, "sites": sites})
                    st.toast(f"✅ 追加完了: {p_name}")
                    time.sleep(0.3)
                    st.rerun()

c_btn1, c_btn2 = st.columns([2, 1])
with c_btn1:
    btn_run = st.button("🔄 最新情報を高速巡回（特設サイト巡回含む）", use_container_width=True)
with c_btn2:
    with st.popover("➕ 特設・速報サイト登録"):
        st.caption("特設サイトURLやブログRSSを登録")
        r_name = st.text_input("サイト名 (例: 眼鏡市場 遊戯王)")
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
                smart_title = get_smart_name(cf_item.get('name'), target_url)
                if target_url.startswith("http"): cc1.link_button(f"👉 {smart_title}", target_url, use_container_width=True)
                else: cc1.write(f"・{smart_title}")
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
            pn = (d.get("standard_name") if (d and isinstance(d, dict)) else None) or h["name"][:25]
            rp = parse_num(d.get("retail_price"), 0) if (d and isinstance(d, dict)) else 0
            mp = parse_num(d.get("market_price"), 0) if (d and isinstance(d, dict)) else 0
            pf = mp - int(mp * 0.1) - 750 - rp if (mp and rp) else 0
            mr = round((pf / mp) * 100, 1) if mp > 0 else 0
            fg = (d.get("genre") if (d and isinstance(d, dict)) else None) or h["genre"]
            ts = parse_num(d.get("trust_score"), 85) if (d and isinstance(d, dict)) else 80
            tr = (d.get("trust_reason") if (d and isinstance(d, dict)) else None) or "特設・先行告知"
            is_tea = (rp == 0 and mp == 0) or (d.get("is_teaser", False) if (d and isinstance(d, dict)) else False)
            raw_dl = d.get("deadline") if (d and isinstance(d, dict)) else None
            sites = [{**s, "created_at": today, "updated_at": today, "deadline_date": s.get("deadline") or raw_dl, "status": "未応募"} for s in (d.get("sites", []) if (d and isinstance(d, dict)) else [{"site_name":"特設","url":h["url"]}])]
            for l in get_links(pn, fg):
                if not any(x["site_name"] == l["site_name"] for x in sites): sites.append({**l, "created_at": today, "updated_at": today, "status": "未応募"})
            match = next((x for x in items if pn in x.get("name","") or clean_url(x.get("url")) == clean_url(h["url"])), None)
            if match:
                update_db(match["id"], {"retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9) if rp else 0, "trust_score": ts, "trust_reason": tr, "is_teaser": is_tea, "updated_at": today})
            else:
                save_db({"id": str(int(time.time()*1000)+done), "name": pn, "url": clean_url(h["url"]), "retail_price": rp, "market_price": mp, "profit": pf, "margin_rate": mr, "break_even": int((rp+750)/0.9) if rp else 0, "sns_genre": fg, "trust_score": ts, "trust_reason": tr, "is_teaser": is_tea, "created_at": today, "updated_at": today, "sites": sites})
    pt.empty(); pb.empty()
    st.success("巡回完了！"); st.rerun()

st.markdown("---")
cf, cs = st.columns([1, 1])
with cf:
    s_gen = st.selectbox("ジャンル", ["すべて", "💄 コフレ", "🃏 TCG", "🤖 プレバン", "👟 スニーカー", "🧸 ホビー", "🎣 釣具", "🌎 海外相場"])
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
    is_tea = item.get("is_teaser", False) or (parse_num(item.get('retail_price'), 0) == 0 and parse_num(item.get('market_price'), 0) == 0)
    pv = parse_num(item.get('profit'), 0)
    badge = "📢【先行予告】" if is_tea else (f"{'🔥' if pv >= 50000 else '💰'} +{pv:,}円" if pv >= 10000 else "")
    with st.expander(f"{get_icon(item.get('sns_genre',''))} {item.get('name','')} {badge} {'【応募中】' if has_app else ''}"):
        ci, cd = st.columns([5, 1])
        ci.caption(f"🛡️ 信頼度: {item.get('trust_score',70)}点（{item.get('trust_reason','通常')}） | 更新: {item.get('updated_at','-')}")
        if cd.button("削除", key=f"del_{item['id']}"): del_db(item["id"]); st.rerun()
        
        c_m1, c_m2 = st.columns(2)
        c_m1.metric("定価", f"¥{parse_num(item.get('retail_price'), 0):,}" if not is_tea else "未発表(詳細待ち)")
        c_m2.metric("相場", f"¥{parse_num(item.get('market_price'), 0):,}" if not is_tea else "相場追跡中")
        c_m3, c_m4 = st.columns(2)
        c_m3.metric("利益目安", f"¥{pv:,}" if not is_tea else "-", f"{item.get('margin_rate',0)}%" if not is_tea else "")
        c_m4.metric("損益分岐", f"¥{parse_num(item.get('break_even'), 0):,}" if not is_tea else "-")
        
        cx1, cx2 = st.columns([1, 1])
        with cx1:
            with st.popover("𝕏 ポスト文面", use_container_width=True):
                dl_found = [s.get("deadline_date") for s in sites if s.get("deadline_date")]
                dl_str = min(dl_found) if dl_found else "公式発表待ち"
                target_hook = get_target_hook(item.get('name',''), item.get('sns_genre',''))
                if is_tea:
                    tw_main = f"{target_hook}\n待望の限定コラボ特設サイトが遂に公開されました！\n限定生産で即完売・後から入手困難になる可能性が非常に高いため、定価確保したい方は要チェックです🔥\n\n📦 対象：{item.get('name','')}\n・詳細/価格：順次発表予定\n・受付予定日：{dl_str}\n\n争奪戦の開始を見逃さないよう【ブックマーク🔖】推奨です！\n\n👇 公式特設・詳細はリプライ欄に記載\n#{item.get('sns_genre','限定コラボ')} #定価確保 #先行速報"
                else:
                    tw_main = f"{target_hook}\n公式抽選・予約受付の注目アイテムです！\n二次流通で高騰する前に、定価で手に入れたい方はお見逃しなく🔥\n\n📦 {item.get('name','')}\n・定価目安：¥{parse_num(item.get('retail_price'), 0):,}\n・市場目安：約¥{parse_num(item.get('market_price'), 0):,}〜\n⏰ 締切目安：{dl_str}\n\n忘れ防止に【ブックマーク🔖】推奨！\n\n👇 応募受付リンクはリプライ欄に記載\n#{item.get('sns_genre','限定品')} #定価購入 #抽選速報"
                tw_rep = "【公式特設・受付リンク】\n" + "\n".join([f"・{s.get('site_name')}: {clean_url(s.get('url'))}" for s in sites[:2]])
                st.text_area("本文", tw_main, height=130, key=f"tw_m_{item['id']}")
                st.link_button("👉 𝕏 投稿画面へ", f"https://twitter.com/intent/tweet?text={up.quote(tw_main)}", use_container_width=True)
                st.text_area("リプライ用", tw_rep, height=65, key=f"tw_r_{item['id']}")
        with cx2:
            if st.button("🔄 相場・締切再取得", key=f"r_{item['id']}", use_container_width=True):
                d = call_gemini(item.get("name",""), clean_url(item.get("url","")), item.get("sns_genre",""), "")
                if d and isinstance(d, dict):
                    rp, mp = parse_num(d.get("retail_price"), 0), parse_num(d.get("market_price"), 0)
                    new_dl = d.get("deadline")
                    for s in sites:
                        if new_dl and not s.get("deadline_date"): s["deadline_date"] = new_dl
                    update_db(item["id"], {"retail_price": rp, "market_price": mp, "profit": mp - int(mp * 0.1) - 750 - rp if (mp and rp) else 0, "sites": sites, "is_teaser": (rp == 0 and mp == 0), "trust_score": parse_num(d.get("trust_score"), 85), "trust_reason": d.get("trust_reason") or "再取得", "updated_at": today})
                    st.rerun()

        for idx, s in enumerate(sites):
            with st.container(border=True):
                st.write(f"🔗 **{s.get('site_name')}** (締切: `{s.get('deadline_date') or '未設定'}`)")
                cs_st, cs_dt = st.columns(2)
                cur_st = s.get("status", "未応募")
                idx_sel = STATUS_OPTS.index(cur_st) if cur_st in STATUS_OPTS else 0
                nst = cs_st.selectbox("状況", STATUS_OPTS, index=idx_sel, key=f"s_{item['id']}_{idx}")
                d_val = parse_date_safe(s.get("deadline_date")) or today_d
                nd = cs_dt.date_input("締切編集", value=d_val, key=f"dt_{item['id']}_{idx}").strftime("%Y-%m-%d")
                if nst != cur_st or (s.get("deadline_date") and nd != s.get("deadline_date")):
                    s["status"], s["deadline_date"], s["updated_at"] = nst, nd, today
           
