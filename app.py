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
def get_default_platform_links(item_name, genre, base_deadline):
    encoded = urllib.parse.quote(item_name)
    links = []
    g_str = str(genre) + str(item_name)
    
    if any(k in g_str for k in ["TCG", "ポケカ", "ポケモン", "ワンピース"]):
        links.append({"site_name": "ポケモンセンターオンライン（抽選・販売）", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "あみあみ（抽選・予約）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "セブンネットショッピング", "url": f"https://7net.omni7.jp/search/?keyword={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "ヨドバシ・ドット・コム", "url": f"https://www.yodobashi.com/?word={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "スニダン（トレカ相場・出品）", "url": f"https://snkrdunk.com/search?keywords={encoded}", "deadline_date": base_deadline})
    elif any(k in g_str for k in ["プレバン", "バンダイ", "フィギュア"]):
        links.append({"site_name": "プレミアムバンダイ公式", "url": f"https://p-bandai.jp/chara/c0001/?utm_source=search&keyword={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "あみあみ公式", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "ビックカメラ.com", "url": f"https://www.biccamera.com/bc/category/?q={encoded}", "deadline_date": base_deadline})
    elif any(k in g_str for k in ["ソフビ", "ホビー"]):
        links.append({"site_name": "まんだらけ公式（ソフビ）", "url": f"https://order.mandarake.co.jp/order/listPage/list?keyword={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "墓場の画廊", "url": "https://store.hakabanogarou.jp/shopbrand/ct10/", "deadline_date": base_deadline})
        links.append({"site_name": "メディコム・トイ公式", "url": "http://www.medicomtoy.co.jp/", "deadline_date": base_deadline})
    else:
        links.append({"site_name": "SNKRS / Nike公式", "url": f"https://www.nike.com/jp/w?q={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "スニダン（スニーカー相場）", "url": f"https://snkrdunk.com/search?keywords={encoded}", "deadline_date": base_deadline})
        links.append({"site_name": "KITH TOKYO / atmos", "url": f"https://www.google.com/search?q={encoded}+抽選", "deadline_date": base_deadline})
    return links

# --- AI解析エンジン（Gemini 3.6 Flash） ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    try:
        client = genai.Client(api_key=gemini_key)
        today = datetime.date.today()
        today_str = today.strftime("%Y-%m-%d")
        default_deadline = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        
        prompt = f"""
本日は {today_str} です。限定アイテム（TCG・ソフビ・プレバン・ホビー・スニーカー）の専門アナリストとして情報解析を行ってください。
情報から「統一された正式商品名」「定価」「予想相場」および、本文に記載されている【すべての応募・予約受付サイトや店舗、それぞれの締切日（YYYY-MM-DD形式）】を抽出してください。
本文中に明確な締切日がない場合は、受付開始から概ね1週間後の日付（例: {default_deadline}）を推計して設定してください。「随時更新」などの曖昧な文字列は禁止し、必ず YYYY-MM-DD 形式の日付にしてください。
必ず以下の純粋なJSONフォーマットのみを出力してください。

【対象】
- タイトル: {name}
- 参照URL: {url}
- ジャンル: {genre}
- 本文: {raw_text[:600]}

【JSONフォーマット】
{{
  "standard_name": "商品名（統一名称・25文字以内）",
  "retail_price": 5500,
  "market_price": 12000,
  "estimated_deadline": "{default_deadline}",
  "rating": "S",
  "comment": "注目案件・即完売必至",
  "extracted_sites": [
    {{
      "site_name": "店舗名・サイト名",
      "url": "{url}",
      "deadline_date": "{default_deadline}"
    }}
  ]
}}
"""
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        
        raw_res = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw_res)
        data["genre"] = genre
        return data
    except Exception:
        return None

# --- クローラー ---
def fetch_patrol_targets():
    targets = []
    queries = [
        ("ポケカ 抽選予約 予約開始", "TCG"),
        ("ワンピースカード 抽選予約 予約開始", "TCG"),
        ("プレミアムバンダイ 受注開始 限定", "プレバン"),
        ("ソフビ 抽選販売 限定", "ソフビ"),
        ("Nike SNKRS スニーカー 抽選", "スニーカー")
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

col_btn1, col_btn2 = st.columns([1, 1])

with col_btn1:
    if st.button("🔄 全自動で最新情報を巡回収集", use_container_width=True):
        progress_text = st.empty()
        progress_bar = st.progress(0)
        
        crawler_hits = fetch_patrol_targets()
        total_steps = len(crawler_hits) if crawler_hits else 1
        all_items = load_db()
        today = datetime.date.today()
        today_str = today.strftime("%Y-%m-%d")
        fallback_deadline = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        new_count = 0
        
        for idx, h in enumerate(crawler_hits):
            percent = int(((idx + 1) / total_steps) * 100)
            progress_text.markdown(f"**速報を巡回中... {percent}%**")
            progress_bar.progress((idx + 1) / total_steps)
            
            parsed = analyze_master_intelligence(h["name"], h["url"], h["genre"], raw_text=h["summary"])
            if parsed:
                prod_name = parsed.get("standard_name") or h["name"][:30]
                f_retail = int(parsed.get("retail_price", 0))
                f_market = int(parsed.get("market_price", 0))
                profit = f_market - int(f_market * 0.10) - 750 - f_retail
                margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                break_even = int((f_retail + 750) / 0.90)
                main_deadline = parsed.get("estimated_deadline", fallback_deadline)

                sites_to_add = []
                for s in parsed.get("extracted_sites", []):
                    s_d = s.get("deadline_date")
                    if not s_d or s_d == "随時更新":
                        s_d = main_deadline
                    sites_to_add.append({
                        "site_name": s.get("site_name", "公式・速報ページ"),
                        "url": s.get("url") or h["url"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": s_d,
                        "status": "未応募"
                    })
                
                default_links = get_default_platform_links(prod_name, h["genre"], main_deadline)
                for d in default_links:
                    if not any(x["site_name"] == d["site_name"] for x in sites_to_add):
                        sites_to_add.append({
                            "site_name": d["site_name"],
                            "url": d["url"],
                            "created_at": today_str,
                            "updated_at": today_str,
                            "deadline_date": d["deadline_date"],
                            "status": "未応募"
                        })

                matched_item = None
                for it in all_items:
                    it_name = it.get("name", "")
                    if prod_name in it_name or it_name in prod_name:
                        matched_item = it
                        break
                
                if matched_item:
                    curr_raw = matched_item.get("sites")
                    if isinstance(curr_raw, str):
                        try:
                            current_sites = json.loads(curr_raw)
                        except Exception:
                            current_sites = []
                    else:
                        current_sites = curr_raw if isinstance(curr_raw, list) else []

                    existing_site_names = [x.get("site_name") for x in current_sites]
                    added = False
                    for s in sites_to_add:
                        if s["site_name"] not in existing_site_names:
                            current_sites.append(s)
                            added = True
                    if added:
                        update_item_in_db(matched_item["id"], {
                            "sites": current_sites,
                            "updated_at": today_str
                        })
                else:
                    new_record = {
                        "id": str(int(datetime.datetime.now().timestamp()) + new_count),
                        "name": prod_name,
                        "url": h["url"],
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
                        "sites": sites_to_add
                    }
                    save_to_db(new_record)
                    all_items.append(new_record)
                    new_count += 1
                    
            time.sleep(0.05)

        progress_bar.empty()
        progress_text.empty()
        st.success("巡回と締切日の更新が完了しました！")
        st.rerun()

with col_btn2:
    with st.popover("➕ 手動で案件を投入"):
        in_name = st.text_input("商品名")
        in_url = st.text_input("公式/速報URL")
        in_post = st.text_area("本文・告知文コピペ")
        in_genre = st.selectbox("ジャンル", ["TCG", "プレバン", "ソフビ", "スニーカー", "その他"])
        if st.button("登録する", use_container_width=True):
            if in_name or in_url or in_post:
                today = datetime.date.today()
                today_str = today.strftime("%Y-%m-%d")
                default_deadline = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

                parsed = analyze_master_intelligence(in_name or in_post[:25], in_url, in_genre, raw_text=in_post)
                prod_name = in_name if in_name else (parsed.get("standard_name") if parsed else in_post[:25])
                
                f_retail = int(parsed.get("retail_price", 0)) if parsed else 5000
                f_market = int(parsed.get("market_price", 0)) if parsed else 12000
                profit = f_market - int(f_market * 0.10) - 750 - f_retail
                margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                break_even = int((f_retail + 750) / 0.90)
                main_deadline = parsed.get("estimated_deadline", default_deadline) if parsed else default_deadline

                sites = []
                if in_url:
                    sites.append({
                        "site_name": "公式・指定URL",
                        "url": in_url,
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": main_deadline,
                        "status": "未応募"
                    })
                for d in get_default_platform_links(prod_name, in_genre, main_deadline):
                    sites.append({
                        "site_name": d["site_name"],
                        "url": d["url"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": d["deadline_date"],
                        "status": "未応募"
                    })

                new_record = {
                    "id": str(int(datetime.datetime.now().timestamp())),
                    "name": prod_name,
                    "url": in_url if in_url else "https://google.com",
                    "retail_price": f_retail,
                    "market_price": f_market,
                    "profit": profit,
                    "margin_rate": margin,
                    "break_even": break_even,
                    "rating": parsed.get("rating", "S") if parsed else "S",
                    "comment": parsed.get("comment", "手動登録案件") if parsed else "手動登録案件",
                    "sns_genre": in_genre,
                    "created_at": today_str,
                    "updated_at": today_str,
                    "sites": sites
                }
                save_to_db(new_record)
                st.success("商品を登録しました！")
                st.rerun()

st.markdown("---")

# --- 商品一覧表示 ---
items = load_db()

normalized_items = []
today = datetime.date.today()
today_str = today.strftime("%Y-%m-%d")
default_d = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

for item in items:
    raw_sites = item.get("sites")
    if isinstance(raw_sites, str):
        try:
            raw_sites = json.loads(raw_sites)
        except Exception:
            raw_sites = []
    elif not isinstance(raw_sites, list):
        raw_sites = []

    updated_needed = False
    if not raw_sites:
        p_name = item.get("name", "")
        p_genre = item.get("sns_genre", "ホビー")
        if item.get("url"):
            raw_sites.append({
                "site_name": "公式・情報元ページ",
                "url": item.get("url"),
                "created_at": item.get("created_at", today_str),
                "updated_at": item.get("updated_at", today_str),
                "deadline_date": default_d,
                "status": "未応募"
            })
        for d in get_default_platform_links(p_name, p_genre, default_d):
            raw_sites.append({
                "site_name": d["site_name"],
                "url": d["url"],
                "created_at": item.get("created_at", today_str),
                "updated_at": item.get("updated_at", today_str),
                "deadline_date": d["deadline_date"],
                "status": "未応募"
            })
        updated_needed = True
    else:
        for s in raw_sites:
            if s.get("deadline_date") in ["随時更新", "公式参照", "未定", None]:
                s["deadline_date"] = default_d
                updated_needed = True

    if updated_needed:
        update_item_in_db(item["id"], {"sites": raw_sites})

    item["sites"] = raw_sites
    normalized_items.append(item)

# 応募中件数のカウント
applying_count = sum(1 for it in normalized_items if any(s.get("status") == "応募中" for s in it.get("sites", [])))

filter_applying = st.checkbox(f"【応募中】がある商品のみ表示（現在: {applying_count} 件）")

if not normalized_items:
    st.info("現在監視中の商品はありません。「全自動で最新情報を巡回収集」を実行してください。")
else:
    for item in normalized_items:
        sites_list = item.get("sites", [])
        has_applying = any(s.get("status") == "応募中" for s in sites_list)

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
                if item.get("comment"):
                    st.caption(f"💡 {item.get('comment')}")
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

            st.markdown(f"#### 📝 申込み・受付サイト一覧（{len(sites_list)} 件）")

            updated_sites = False
            for s_idx, s in enumerate(sites_list):
                with st.container():
                    site_target_url = s.get("url") if s.get("url") else "https://google.com"
                    st.markdown(f"""
                    <div class='site-card'>
                        <strong>🔗 {s.get('site_name', '受付サイト')}</strong><br>
                        <span class='date-text'>初回記載日: {s.get('created_at', '-')} ｜ 更新日: {s.get('updated_at', '-')} ｜ 締切日: <b>{s.get('deadline_date', '未設定')}</b></span>
                    </div>
       
