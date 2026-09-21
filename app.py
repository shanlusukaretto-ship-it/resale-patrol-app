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
        final_name = name.strip()[:18]
    else:
        domain = up.urlparse(target_url).netloc.replace("www.", "")
        final_name = "丸井/パルワールド" if "0101" in target_url or "AKhZ" in target_url else ("眼鏡市場 遊戯王" if "megane" in target_url else domain[:15] or "特設サイト")
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
            {"site_name": "阪急うめだコスメ", "url": f"https://web.hh-online.jp/hankyu-beauty/goods/list.html?shop=hb&keyword={e}", "deadline_date": None}
        ]
    if any(k in t for k in ["TCG", "ポケカ", "ワンピ", "遊戯王"]):
        return [
            {"site_name": "あみあみ予約", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={e}", "deadline_date": None},
            {"site_name": "スニダン相場", "url": f"https://snkrdunk.com/search?keywords={e}", "deadline_date": None}
        ]
    if "プレバン" in t: return [{"site_name": "プレバン公式", "url": f"https://p-bandai.jp/chara/c0001/?keyword={e}", "deadline_date": None}]
    if any(k in t for k in ["スニーカー", "NIKE"]): return [{"site_name": "SNKRS", "url": f"https://www.nike.com/jp/w?q={e}", "deadline_date": None}]
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
        p = f"本日は{td}。以下の告知文から限定品情報を解析しJSON出力せよ。商品名,定価,予想相場,受付締切日(YYYY-MM-DD),リンクURL,ジャンル(TCG/プレバン/スニーカー/コフレ/ホビー/釣具/海外相場/その他)。不明な締切やURLはnull。対象:\n{tweet_text}\n形式:{{\"standard_name\":\"商品名\",\"retail_price\":5000,\"market_price\":15000,\"deadline\":null,\"genre\":\"ホビー\",\"url\":\"URLまたはnull\",\"trust_score\":95,\"trust_reason\":\"AI抽出\"}}"
        res = genai.Client(api_key=gk).models.generate_content(model="gemini-3.6-flash", contents=p)
        return json.loads(res.text.strip().replace("```json","").replace("
