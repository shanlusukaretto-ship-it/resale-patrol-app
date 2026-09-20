import streamlit as st
import datetime
import urllib.parse
import json
import feedparser
from google import genai
from supabase import create_client, Client

st.set_page_config(
    page_title="プレミア価格サーチ",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 転売クエスト風のカード・バッジCSS
st.markdown("""
<style>
    .quest-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
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
    .badge-loss {
        background-color: #fee2e2;
        color: #991b1b;
        font-size: 0.85rem;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: bold;
    }
    .price-label {
        font-size: 0.8rem;
        color: #64748b;
        margin-bottom: -2px;
    }
    .price-val {
        font-size: 1.15rem;
        font-weight: 800;
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
            res = supabase.table("items").select("*").order("deadline_date", desc=False).execute()
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

def delete_from_db(item_id):
    if supabase:
        try:
            supabase.table("items").delete().eq("id", item_id).execute()
        except Exception:
            pass
    st.session_state.monitored_items = [x for x in st.session_state.monitored_items if str(x.get("id")) != str(item_id)]

# --- AI解析エンジン（Gemini 3.6 Flash） ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    try:
        client = genai.Client(api_key=gemini_key)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
本日は {today_str} です。あなたは限定アイテム（ソフビ、TCG、プレバン限定品、スニーカー、ホビー）の利益・相場予測アナリストです。
情報をもとに、定価、予想転売相場、抽選締切日、おすすめ度を推計し、純粋なJSONのみ出力してください。

【対象】
- タイトル: {name}
- URL: {url}
- ジャンル: {genre}
- 本文: {raw_text[:400]}

【JSONフォーマット】
{{
  "name": "商品名（25文字以内で分かりやすく）",
  "retail_price": 5500,
  "market_price": 12000,
  "deadline_date": "{today_str}",
  "rating": "S",
  "comment": "即完売・高プレ値期待"
}}
"""
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        
        txt = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(txt)
        data["url"] = url if url else "https://google.com"
        data["sns_genre"] = genre
        return data
    except Exception:
        return None

# --- クローラー ---
def fetch_patrol_targets():
    targets = []
    queries = [
        ("ワンピースカード 抽選", "TCG"),
        ("ポケモンカード 予約抽選", "TCG"),
        ("プレミアムバンダイ 限定 予約", "プレバン"),
        ("ソフビ 抽選販売", "ソフビ"),
        ("Nike SNKRS 抽選", "スニーカー")
    ]
    for q, genre in queries:
        encoded_q = urllib.parse.quote(q)
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={encoded_q}&hl=ja&gl=JP&ceid=JP:ja")
        for entry in feed.entries[:3]:
            targets.append({
                "name": entry.title,
                "url": entry.link,
                "genre": genre,
                "summary": getattr(entry, "summary", entry.title)
            })
    return targets

# --- ヘッダー ---
st.title("⚔️ 転売・限定品自衛クエスト")
st.caption("スニダン相場・TCG・プレバン・限定ソフビの定価自衛＆プレミア案件ダッシュボード")

col_btn1, col_btn2 = st.columns([1, 1])
with col_btn1:
    if st.button("🔄 全自動で最新速報を巡回収集", use_container_width=True):
        with st.spinner("速報RSSを巡回し、Geminiで利益推計中..."):
            existing_urls = [x.get("url") for x in load_db()]
            crawler_hits = fetch_patrol_targets()
            new_count = 0
            for h in crawler_hits:
                if h["url"] not in existing_urls:
                    parsed = analyze_master_intelligence(h["name"], h["url"], h["genre"], raw_text=h["summary"])
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
                            "retail_price": f_retail,
                            "market_price": f_market,
                            "profit": profit,
                            "margin_rate": margin,
                            "break_even": break_even,
                            "rating": parsed.get("rating", "A"),
                            "comment": parsed.get("comment", ""),
                            "sns_genre": parsed["sns_genre"]
                        }
                        save_to_db(record)
                        new_count += 1
            if new_count > 0:
                st.success(f"新たに {new_count} 件の案件を追加しました！")
                st.rerun()
            else:
                st.info("新規案件はありませんでした。")

with col_btn2:
    with st.popover("➕ 手動で案件を投入"):
        in_url = st.text_input("公式・告知URL")
        in_post = st.text_area("本文・告知文コピペ")
        in_genre = st.selectbox("ジャンル", ["TCG", "ソフビ", "プレバン", "スニーカー", "その他"])
        if st.button("AI解析して追加", use_container_width=True):
            if in_url or in_post:
                parsed = analyze_master_intelligence(in_post[:25] or "新規案件", in_url, in_genre, raw_text=in_post)
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
                        "retail_price": f_retail,
                        "market_price": f_market,
                        "profit": profit,
                        "margin_rate": margin,
                        "break_even": break_even,
                        "rating": parsed.get("rating", "A"),
                        "comment": parsed.get("comment", ""),
                        "sns_genre": parsed["sns_genre"]
                    }
                    save_to_db(record)
                    st.success("追加しました！")
                    st.rerun()

st.markdown("---")

# --- 案件カード表示（転売クエスト風） ---
items = load_db()
st.subheader(f"🔥 ピックアップ案件一覧 ({len(items)}件)")

if not items:
    st.info("現在監視中の案件はありません。「全自動で最新速報を巡回収集」を実行してください。")
else:
    for item in items:
        # カードコンテナ
        with st.container(border=True):
            head_col, del_col = st.columns([6, 1])
            with head_col:
                genre = item.get("sns_genre", "ホビー")
                rating = item.get("rating", "A")
                st.markdown(f"<span class='badge-genre'>{genre}</span> ｜ **期待度: ランク {rating}**", unsafe_allow_html=True)
                st.markdown(f"### {item.get('name')}")
                if item.get("comment"):
                    st.caption(f"💡 {item.get('comment')}")
            with del_col:
                if st.button("🗑️", key=f"del_{item['id']}", help="削除"):
                    delete_from_db(item["id"])
                    st.rerun()

            # 価格パネル
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.markdown("<p class='price-label'>定価（税込）</p>", unsafe_allow_html=True)
                st.markdown(f"<p class='price-val'>¥{item.get('retail_price', 0):,}</p>", unsafe_allow_html=True)
            with m2:
                st.markdown("<p class='price-label'>予想相場</p>", unsafe_allow_html=True)
                st.markdown(f"<p class='price-val'>¥{item.get('market_price', 0):,}</p>", unsafe_allow_html=True)
            with m3:
                profit = item.get("profit", 0)
                margin = item.get("margin_rate", 0)
                st.markdown("<p class='price-label'>見込み利益 (利益率)</p>", unsafe_allow_html=True)
                badge_class = "badge-profit" if profit > 0 else "badge-loss"
                st.markdown(f"<span class='{badge_class}'>+{profit:,} 円 ({margin}%)</span>", unsafe_allow_html=True)
            with m4:
                st.markdown("<p class='price-label'>損益分岐ライン</p>", unsafe_allow_html=True)
                st.markdown(f"<p class='price-val' style='color:#dc2626;'>¥{item.get('break_even', 0):,}</p>", unsafe_allow_html=True)

            st.write("")

            # アクションボタン群
            btn_c1, btn_c2, btn_c3 = st.columns([1, 1, 1])
            kw = urllib.parse.quote(item.get("name", ""))
            with btn_c1:
                st.link_button("👟 スニダン相場を見る", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
            with btn_c2:
                st.link_button("🔴 メルカリ相場を見る", f"https://jp.mercari.com/search?keyword={kw}", use_container_width=True)
            with btn_c3:
                st.link_button("⚡ 公式抽選・販売ページへ", item.get("url", "https://google.com"), use_container_width=True)
