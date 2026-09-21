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

def load_custom_rss():
    if sb:
        try:
            res = sb.table("items").select("id, name, url").eq("sns_genre", "カスタムRSS").execute().data
            if res: return res
        except: pass
    return st.session_state.get("custom_rss", [])

def add_custom_rss(name, url):
    if not url: return
    it = {"id": f"rss_{int(time.time())}", "name": name or "速報サイト", "url": url, "sns_genre": "カスタムRSS"}
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
        p = f"本日は{td}。以下のXポストや告知文から限定品情報を解析しJSON出力せよ。商品名、定価、予想相場、受付締切日(YYYY-MM-DD)、リンクURL、ジャンル(TCG/プレバン/スニーカー/ホビー/ソフビ/釣具/海外相場/カメラ/キャンプ/その他)。不明な締切やURLはnull。対象文:\n{tweet_text}\n形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"TCG\",\"url\":\"URLまたはnull\",\"trust_score\":95,\"trust_reason\":\"X/告知AI抽出\"}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("
