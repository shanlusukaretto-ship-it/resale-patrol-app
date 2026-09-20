import streamlit as st
import datetime
import urllib.parse
import json
import requests
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai
from supabase import create_client, Client

st.set_page_config(
    page_title="自律巡回・定価自衛ダッシュボード",
    page_icon="⚡",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# スタイル定義
st.markdown("""
<style>
    .badge-auto { color: #155724; background-color: #d4edda; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.8em; }
    .badge-x { color: #004085; background-color: #cce5ff; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.8em; }
    .genre-badge { background-color: #e8daef; color: #5b2c6f; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
    .date-badge { background-color: #fff3cd; color: #856404; padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 0.85em; }
    .date-expired { background-color: #e2e3e5; color: #383d41; padding: 3px 6px; border-radius: 4px; font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)

# --- 設定管理 ---
with st.sidebar:
    st.header("⚙️ システム設定")
    gemini_key = st.secrets.get("GEMINI_API_KEY", st.text_input("Gemini API Key", type="password"))
    sb_url = st.secrets.get("SUPABASE_URL", st.text_input("Supabase URL"))
    sb_key = st.secrets.get("SUPABASE_KEY", st.text_input("Supabase Anon Key", type="password"))
    discord_webhook = st.secrets.get("DISCORD_WEBHOOK_URL", "")

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
            res = supabase.table("items").select("*").order("deadline_date", desc=False).execute()
            return res.data
        except Exception as e:
            st.warning(f"DB読み込みフォールバック: {e}")
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

def delete_from_db(item_id):
    if supabase:
        try:
            supabase.table("items").delete().eq("id", item_id).execute()
        except Exception:
            pass
    st.session_state.monitored_items = [x for x in st.session_state.monitored_items if str(x.get("id")) != str(item_id)]

# --- AI解析エンジン ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        st.error("Gemini API Keyが設定されていません。Secretsを確認してください。")
        return None

    try:
        genai.configure(api_key=gemini_key)
        
        # モデル取得の試行
        target_model = None
        for m_name in ["gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-1.0-pro", "gemini-pro"]:
            try:
                target_model = genai.GenerativeModel(m_name)
                break
            except Exception:
                continue

        if not target_model:
            target_model = genai.GenerativeModel("gemini-pro")

        today_str = datetime.date.today().strftime("%Y-%m-%d")
        prompt = f"""
本日は {today_str} です。あなたは限定アイテム（ソフビ、TCG、プレバン限定品）の専門アナリストです。
提供された情報から相場・定価・スケジュールを推計し、必ず以下の純粋なJSONのみを出力してください（マークダウンのバッククォートも不要です）。

【対象】
- 名称/タイトル: {name}
- URL: {url}
- ジャンル: {genre}
- 告知文: {raw_text}

【必須JSONフォーマット】
{{
  "name": "商品名・タイトル",
  "url": "公式受付URL（不明なら推測URLまたは https://google.com）",
  "retail_price": 5000,
  "market_price": 12000,
  "deadline_date": "{today_str}",
  "result_date": "{today_str}",
  "difficulty": "★★★☆☆",
  "market_trend": "高需要・定価超え推移"
}}
"""
        response = target_model.generate_content(prompt)
        txt = response.text.strip()
        txt = txt.replace("```json", "").replace("```", "").strip()
        data = json.loads(txt)

        if not data.get("url"):
            data["url"] = url if url else "https://google.com"
        data["sns_genre"] = genre
        return data
    except Exception as e:
        st.error(f"AI解析エラー詳細: {e}")
        return None

# --- UIメイン ---
st.title("⚡ 自律巡回＆定価自衛ボード")

# 1. 自動巡回実行ボタン
if st.button("🔄 全自動マスター巡回", use_container_width=True):
    with st.spinner("情報スキャン中..."):
        sample_hits = [
            {"name": "ワンピースカード 新時代の主役 BOX", "url": "https://www.onepiece-cardgame.com/", "genre": "TCG・トレカ", "type": "公式巡回"},
            {"name": "墓場の画廊限定 ソフビ怪獣シリーズ", "url": "https://store.hakabanogarou.jp/", "genre": "ソフビ・ホビー", "type": "公式巡回"}
        ]
        new_count = 0
        existing_urls = [x.get("url") for x in load_db()]
        for h in sample_hits:
            if h["url"] not in existing_urls:
                parsed = analyze_master_intelligence(h["name"], h["url"], h["genre"], raw_text="公式事前抽選受付")
                if parsed:
                    f_retail = int(parsed.get("retail_price", 0))
                    f_market = int(parsed.get("market_price", 0))
                    profit = f_market - int(f_market * 0.10) - 750 - f_retail
                    margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                    break_even = int((f_retail + 750) / 0.90)

                    record = {
                        "id": str(int(datetime.datetime.now().timestamp()) + new_count),
                        "name": parsed["name"],
                        "url": parsed["url"],
                        "deadline_date": parsed["deadline_date"],
                        "result_date": parsed["result_date"],
                        "retail_price": f_retail,
                        "extra_cost": 0,
                        "total_cost": f_retail,
                        "market_price": f_market,
                        "profit": profit,
                        "margin_rate": margin,
                        "break_even": break_even,
                        "difficulty": parsed["difficulty"],
                        "market_trend": parsed["market_trend"],
                        "sns_genre": parsed["sns_genre"],
                        "source_type": h["type"]
                    }
                    save_to_db(record)
                    new_count += 1
        if new_count > 0:
            st.success(f"新たに {new_count} 件を自動取得・AI解析しました！")
            st.rerun()
        else:
            st.info("すべての案件は取得済みです。")

# 2. 手動投入
with st.expander("📥 Xポストや個別URLを手動で投入する", expanded=True):
    in_url = st.text_input("公式/告知URL", placeholder="https://...")
    in_post = st.text_area("Xのポスト文 または 告知本文（コピペ）", placeholder="【公式】ワンピースカード新弾 抽選受付開始...", height=80)
    in_genre = st.selectbox("ジャンル指定", ["TCG・トレカ", "ソフビ・ホビー", "プレバン限定", "Supreme・ストリート"])
    
    if st.button("🪄 AI解析してリストに追加", use_container_width=True):
        if not in_url and not in_post:
            st.warning("URLか告知文を入力してください。")
        else:
            with st.spinner("AI解析実行中..."):
                item_name = in_post[:30] if in_post else "新規限定品"
                parsed = analyze_master_intelligence(item_name, in_url, in_genre, raw_text=in_post)
                if parsed:
                    f_retail = int(parsed.get("retail_price", 0))
                    f_market = int(parsed.get("market_price", 0))
                    profit = f_market - int(f_market * 0.10) - 750 - f_retail
                    margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                    break_even = int((f_retail + 750) / 0.90)

                    record = {
                        "id": str(int(datetime.datetime.now().timestamp())),
                        "name": parsed["name"],
                        "url": parsed["url"],
                        "deadline_date": parsed["deadline_date"],
                        "result_date": parsed["result_date"],
                        "retail_price": f_retail,
                        "extra_cost": 0,
                        "total_cost": f_retail,
                        "market_price": f_market,
                        "profit": profit,
                        "margin_rate": margin,
                        "break_even": break_even,
                        "difficulty": parsed["difficulty"],
                        "market_trend": parsed["market_trend"],
                        "sns_genre": parsed["sns_genre"],
                        "source_type": "手動/X投入"
                    }
                    save_to_db(record)
                    st.success(f"「{parsed['name']}」を追加しました！")
                    st.rerun()

st.markdown("---")

# 3. リスト一覧
items = load_db()
st.subheader(f"📋 監視中案件（{len(items)} 件）")

if not items:
    st.info("監視中の案件はありません。")
else:
    for item in items:
        with st.container():
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(f"### {item['name']}")
                st.caption(f"ジャンル: {item.get('sns_genre')} ｜ 締切: {item.get('deadline_date')}")
            with c2:
                if st.button("🗑️", key=f"del_{item['id']}"):
                    delete_from_db(item["id"])
                    st.rerun()

            p1, p2, p3 = st.columns(3)
            p1.caption("定価")
            p1.write(f"{item.get('retail_price', 0):,} 円")
            p2.caption("見込み差額")
            p2.markdown(f":green[**+{item.get('profit', 0):,} 円**]")
            p3.caption("損益分岐ライン")
            p3.write(f"⚠️ {item.get('break_even', 0):,} 円")

            kw = urllib.parse.quote(item.get("name", ""))
            sc1, sc2 = st.columns(2)
            sc1.link_button("スニダンで相場確認", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
            sc2.link_button("メルカリで確認", f"https://jp.mercari.com/search?keyword={kw}", use_container_width=True)
            
            st.link_button("🔗 公式受付ページへ行く", item.get("url", "https://google.com"), use_container_width=True)
            st.markdown("---")
