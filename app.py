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

# --- AI解析エンジン（Gemini 3.6 Flash） ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    try:
        client = genai.Client(api_key=gemini_key)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
本日は {today_str} です。限定アイテム（ソフビ、TCG、プレバン、ホビー）のアナリストとして情報解析を行ってください。
必ず純粋なJSONのみを出力してください。

【対象】
- タイトル: {name}
- 参照URL: {url}
- ジャンル: {genre}
- 本文抜粋: {raw_text[:400]}

【JSONフォーマット】
{{
  "name": "商品名（統一名称・25文字以内）",
  "site_name": "サイト名・受付店舗名（例：プレミアムバンダイ、あみあみ、ポケモンセンター等）",
  "retail_price": 5500,
  "market_price": 12000,
  "deadline_date": "{today_str}",
  "rating": "S",
  "comment": "注目案件"
}}
"""
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        
        txt = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(txt)
        data["url"] = url if url else "https://google.com"
        data["genre"] = genre
        return data
    except Exception:
        return None

# --- クローラー ---
def fetch_patrol_targets():
    targets = []
    queries = [
        ("ワンピースカード 抽選予約", "TCG"),
        ("ポケモンカード 抽選予約", "TCG"),
        ("プレミアムバンダイ 受注開始", "プレバン"),
        ("ソフビ 抽選販売", "ソフビ")
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
st.title("プレミア商品確認ツール")

# 巡回＆投入コントロール
col_btn1, col_btn2 = st.columns([1, 1])

with col_btn1:
    if st.button("🔄 全自動で最新情報を巡回収集", use_container_width=True):
        progress_text = st.empty()
        progress_bar = st.progress(0)
        
        crawler_hits = fetch_patrol_targets()
        total_steps = len(crawler_hits) if crawler_hits else 1
        all_items = load_db()
        existing_urls = [x.get("url") for x in all_items]
        
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        new_count = 0
        
        for idx, h in enumerate(crawler_hits):
            percent = int(((idx + 1) / total_steps) * 100)
            progress_text.markdown(f"**速報を巡回中... {percent}%**")
            progress_bar.progress((idx + 1) / total_steps)
            
            if h["url"] not in existing_urls:
                parsed = analyze_master_intelligence(h["name"], h["url"], h["genre"], raw_text=h["summary"])
                if parsed:
                    f_retail = int(parsed.get("retail_price", 0))
                    f_market = int(parsed.get("market_price", 0))
                    profit = f_market - int(f_market * 0.10) - 750 - f_retail
                    margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                    break_even = int((f_retail + 750) / 0.90)

                    site_entry = {
                        "site_name": parsed.get("site_name", "公式/速報サイト"),
                        "url": parsed["url"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": parsed["deadline_date"],
                        "status": "未応募"
                    }

                    matched_item = next((it for it in all_items if it.get("name") == parsed["name"]), None)
                    
                    if matched_item:
                        current_sites = matched_item.get("sites") or []
                        current_sites.append(site_entry)
                        update_item_in_db(matched_item["id"], {
                            "sites": current_sites,
                            "updated_at": today_str
                        })
                    else:
                        new_record = {
                            "id": str(int(datetime.datetime.now().timestamp()) + new_count),
                            "name": parsed["name"],
                            "url": parsed["url"],
                            "retail_price": f_retail,
                            "market_price": f_market,
                            "profit": profit,
                            "margin_rate": margin,
                            "break_even": break_even,
                            "rating": parsed.get("rating", "A"),
                            "comment": parsed.get("comment", ""),
                            "sns_genre": parsed["genre"],
                            "created_at": today_str,
                            "updated_at": today_str,
                            "sites": [site_entry]
                        }
                        save_to_db(new_record)
                        all_items.append(new_record)
                        
                    new_count += 1
            time.sleep(0.1)

        progress_bar.empty()
        progress_text.empty()
        
        if new_count > 0:
            st.success(f"巡回完了：新たに {new_count} 件の情報を更新しました！")
            st.rerun()
        else:
            st.info("最新の案件はすべて反映済みです。")

with col_btn2:
    with st.popover("➕ 手動で案件を投入"):
        in_name = st.text_input("商品名（空欄ならAIが自動判定）")
        in_url = st.text_input("申込みURL / 公式URL")
        in_post = st.text_area("告知文・投稿テキスト")
        in_genre = st.selectbox("ジャンル", ["TCG", "プレバン", "ソフビ", "スニーカー", "その他"])
        if st.button("登録する", use_container_width=True):
            if in_url or in_post:
                parsed = analyze_master_intelligence(in_name or in_post[:25], in_url, in_genre, raw_text=in_post)
                if parsed:
                    today_str = datetime.date.today().strftime("%Y-%m-%d")
                    f_retail = int(parsed.get("retail_price", 0))
                    f_market = int(parsed.get("market_price", 0))
                    profit = f_market - int(f_market * 0.10) - 750 - f_retail
                    margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                    break_even = int((f_retail + 750) / 0.90)

                    site_entry = {
                        "site_name": parsed.get("site_name", "手動登録サイト"),
                        "url": parsed["url"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": parsed["deadline_date"],
                        "status": "未応募"
                    }

                    new_record = {
                        "id": str(int(datetime.datetime.now().timestamp())),
                        "name": parsed["name"],
                        "url": parsed["url"],
                        "retail_price": f_retail,
                        "market_price": f_market,
                        "profit": profit,
                        "margin_rate": margin,
                        "break_even": break_even,
                        "rating": parsed.get("rating", "A"),
                        "comment": parsed.get("comment", ""),
                        "sns_genre": parsed["genre"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "sites": [site_entry]
                    }
                    save_to_db(new_record)
                    st.success("登録完了しました！")
                    st.rerun()

st.markdown("---")

# --- 商品一覧表示 ---
items = load_db()
filter_applying = st.checkbox("【応募中】がある商品のみ表示")

if not items:
    st.info("現在監視中の商品はありません。「全自動で最新情報を巡回収集」を実行してください。")
else:
    for item in items:
        raw_sites = item.get("sites") or []
        if not raw_sites and item.get("url"):
            raw_sites = [{
                "site_name": "公式受付",
                "url": item.get("url"),
                "created_at": item.get("created_at", "2026-09-21"),
                "updated_at": item.get("updated_at", "2026-09-21"),
                "deadline_date": item.get("deadline_date", "未定"),
                "status": "未応募"
            }]

        has_applying = any(s.get("status") == "応募中" for s in raw_sites)

        if filter_applying and not has_applying:
            continue

        created_dt = item.get("created_at", "-")
        updated_dt = item.get("updated_at", "-")
        applying_badge = "<span class='badge-applying'>応募中あり</span>" if has_applying else ""

        profit_val = item.get("profit", 0)
        profit_display = f"+{profit_val:,}円" if profit_val else ""
        expander_title = f"📦 {item.get('name', '未設定')}　{profit_display}"

        with st.expander(expander_title, expanded=False):
            col_info, col_del = st.columns([5, 1])
            with col_info:
                st.markdown(f"**ジャンル**: <span class='badge-genre'>{item.get('sns_genre', '一般')}</span> {applying_badge}", unsafe_allow_html=True)
                st.markdown(f"<span class='date-text'>初回掲載日: {created_dt} ｜ 最終更新日: {updated_dt}</span>", unsafe_allow_html=True)
            with col_del:
                if st.button("削除", key=f"del_prod_{item['id']}", help="商品ごと削除"):
                    delete_from_db(item["id"])
                    st.rerun()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("定価", f"¥{item.get('retail_price', 0):,}")
            m2.metric("予想相場", f"¥{item.get('market_price', 0):,}")
            m3.metric("見込み利益", f"¥{item.get('profit', 0):,}", f"{item.get('margin_rate', 0)}%")
            m4.metric("損益分岐", f"¥{item.get('break_even', 0):,}")

            kw = urllib.parse.quote(item.get("name", ""))
            q_col1, q_col2 = st.columns(2)
            q_col1.link_button("👟 スニダン相場を確認", f"https://snkrdunk.com/search?keywords={kw}", use_container_width=True)
            q_col2.link_button("🔴 メルカリ直近落札相場", f"https://jp.mercari.com/search?keyword={kw}&status=sold_out", use_container_width=True)

            st.markdown("#### 📝 申込み・抽選サイト一覧")

            updated_sites = False
            for s_idx, s in enumerate(raw_sites):
                with st.container():
                    st.markdown(f"""
                    <div class='site-card'>
                        <strong>🔗 {s.get('site_name', '受付サイト')}</strong><br>
                        <span class='date-text'>初回記載日: {s.get('created_at', '-')} ｜ 更新日: {s.get('updated_at', '-')} ｜ 締切日: <b>{s.get('deadline_date', '未定')}</b></span>
                    </div>
                    """, unsafe_allow_html=True)

                    c_status, c_link = st.columns([2, 2])
                    with c_status:
                        current_st = s.get("status", "未応募")
                        status_list = ["未応募", "応募中", "当選", "落選"]
                        idx_val = status_list.index(current_st) if current_st in status_list else 0
                        new_st = st.selectbox(
                            "応募ステータス",
                            status_list,
                            index=idx_val,
                            key=f"status_{item['id']}_{s_idx}"
                        )
                        if new_st != current_st:
                            s["status"] = new_st
                            s["updated_at"] = datetime.date.today().strftime("%Y-%m-%d")
                            updated_sites = True

                    with c_link:
                        st.write("")
                        st.link_button("👉 受付ページへ飛ぶ", s.get("url", "https://google.com"), use_container_width=True)

            if updated_sites:
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                update_item_in_db(item["id"], {
                    "sites": raw_sites,
                    "updated_at": today_str
                })
                st.rerun()
