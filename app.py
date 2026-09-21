import streamlit as st
import datetime
import urllib.parse
import json
import time
import feedparser
from google import genai
from supabase import create_client, Client

st.set_page_config(
    page_title="プレミア商品確認ツール",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# スタイル定義
st.markdown("""
<style>
    .badge-applying {
        background-color: #ff4b4b;
        color: white;
        padding: 3px 8px;
        border-radius: 6px;
        font-weight: bold;
        font-size: 0.8rem;
        margin-left: 8px;
    }
    .badge-genre {
        background-color: #f1f5f9;
        color: #475569;
        font-size: 0.75rem;
        padding: 3px 8px;
        border-radius: 6px;
        font-weight: 600;
    }
    .badge-profit {
        background-color: #dcfce7;
        color: #166534;
        font-size: 0.85rem;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: bold;
    }
    .site-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 8px;
    }
    .date-text {
        font-size: 0.8rem;
        color: #64748b;
    }
</style>
""", unsafe_allow_html=True)

# --- 設定管理 ---
gemini_key = st.secrets.get("GEMINI_API_KEY", "")
sb_url = st.secrets.get("SUPABASE_URL", "")
sb_key = st.secrets.get("SUPABASE_KEY", "")

supabase: Client = None
if sb_url and sb_key:
    try:
        supabase = create_client(sb_url, sb_key)
    except Exception as e:
        st.sidebar.error(f"Supabase接続エラー: {e}")

if "monitored_items" not in st.session_state:
    st.session_state.monitored_items = []

def load_db():
    if supabase:
        try:
            res = supabase.table("items").select("*").order("id", desc=True).execute()
            return res.data
        except Exception:
            return st.session_state.monitored_items
    return st.session_state.monitored_items

def save_to_db(item):
    if supabase:
        try:
            supabase.table("items").insert(item).execute()
        except Exception as e:
            st.error(f"Supabase保存エラー: {e}")
            st.session_state.monitored_items.append(item)
    else:
        st.session_state.monitored_items.append(item)

def update_item_in_db(item_id, update_data):
    if supabase:
        try:
            supabase.table("items").update(update_data).eq("id", item_id).execute()
        except Exception:
            pass

def delete_from_db(item_id):
    if supabase:
        try:
            supabase.table("items").delete().eq("id", item_id).execute()
        except Exception:
            pass
    st.session_state.monitored_items = [x for x in st.session_state.monitored_items if str(x.get("id")) != str(item_id)]

# 主要ショップの検索リンク補完ジェネレーター
def get_default_platform_links(item_name, genre):
    encoded = urllib.parse.quote(item_name)
    links = []
    if "TCG" in genre or "ポケカ" in item_name or "ワンピース" in item_name:
        links.append({"site_name": "ポケモンセンターオンライン（検索）", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "あみあみ（抽選・予約検索）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "セブンネットショッピング", "url": f"https://7net.omni7.jp/search/?keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "ヨドバシ・ドット・コム", "url": f"https://www.yodobashi.com/?word={encoded}", "deadline_date": "随時更新"})
    elif "プレバン" in genre:
        links.append({"site_name": "プレミアムバンダイ公式", "url": f"https://p-bandai.jp/chara/c0001/?utm_source=search&keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "あみあみ公式", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": "随時更新"})
    elif "ソフビ" in genre:
        links.append({"site_name": "まんだらけ公式（ソフビ）", "url": f"https://order.mandarake.co.jp/order/listPage/list?keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "墓場の画廊", "url": f"https://store.hakabanogarou.jp/shopbrand/ct10/", "deadline_date": "随時更新"})
    return links

# --- AI解析エンジン（複数サイト抽出 ＆ 正式名統一） ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    try:
        client = genai.Client(api_key=gemini_key)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
本日は {today_str} です。限定アイテム（TCG・ソフビ・プレバン・ホビー）の専門アナリストとして情報解析を行ってください。
情報から「統一された正式商品名」「定価」「予想相場」および、本文に記載されている【すべての応募・予約受付サイトや店舗】をリストで抽出してください。
必ず以下の純粋なJSONフォーマットのみを出力してください。

【対象】
- タイトル: {name}
- 参照URL: {url}
- ジャンル: {genre}
- 本文: {raw_text[:600]}

【JSONフォーマット】
{{
  "standard_name": "統一商品名（例：ポケモンカードゲーム 30th CELEBRATION BOX 等、揺れのない名称）",
  "retail_price": 5500,
  "market_price": 12000,
  "rating": "S",
  "comment": "注目案件・即完売必至",
  "extracted_sites": [
    {{
      "site_name": "店舗名・サイト名（例：ポケモンセンターオンライン、ヨドバシ、ゲオ 等）",
      "url": "{url}",
      "deadline_date": "{today_str}"
    }}
  ]
}}
"""
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        
        txt = response.text.strip().replace("```json", "").replace("
