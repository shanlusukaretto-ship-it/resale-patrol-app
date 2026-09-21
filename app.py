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
def get_default_platform_links(item_name, genre):
    encoded = urllib.parse.quote(item_name)
    links = []
    if any(k in genre or k in item_name for k in ["TCG", "ポケカ", "ワンピース"]):
        links.append({"site_name": "ポケモンセンターオンライン（検索）", "url": f"https://www.pokemoncenter-online.com/?main_page=product_list&keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "あみあみ（抽選・予約検索）", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "セブンネットショッピング", "url": f"https://7net.omni7.jp/search/?keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "ヨドバシ・ドット・コム", "url": f"https://www.yodobashi.com/?word={encoded}", "deadline_date": "随時更新"})
    elif "プレバン" in genre or "バンダイ" in genre:
        links.append({"site_name": "プレミアムバンダイ公式", "url": f"https://p-bandai.jp/chara/c0001/?utm_source=search&keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "あみあみ公式", "url": f"https://www.amiami.jp/top/page/c/search.html?s_keywords={encoded}", "deadline_date": "随時更新"})
    elif "ソフビ" in genre:
        links.append({"site_name": "まんだらけ公式（ソフビ）", "url": f"https://order.mandarake.co.jp/order/listPage/list?keyword={encoded}", "deadline_date": "随時更新"})
        links.append({"site_name": "墓場の画廊", "url": "https://store.hakabanogarou.jp/shopbrand/ct10/", "deadline_date": "随時更新"})
    else:
        links.append({"site_name": "スニダン（検索）", "url": f"https://snkrdunk.com/search?keywords={encoded}", "deadline_date": "随時更新"})
    return links

# --- AI解析エンジン（Gemini 3.6 Flash） ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    try:
        client = genai.Client(api_key=gemini_key)
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        prompt = f"""
本日は {today_str} です。限定アイテム（TCG・ソフビ・プレバン・ホビー・スニーカー）の専門アナリストとして情報解析を行ってください。
情報から「統一された正式商品名」「定価」「予想相場」および、本文に記載されている【すべての応募・予約受付サイトや店舗】をリストで抽出してください。
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
  "rating": "S",
  "comment": "注目案件・即完売必至",
  "extracted_sites": [
    {{
      "site_name": "店舗名・サイト名",
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
        
        raw_res = response.text.strip()
        raw_res = raw_res.replace("```json", "")
        raw_res = raw_res.replace("```", "")
        clean_json = raw_res.strip()
        
        data = json.loads(clean_json)
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
        today_str = datetime.date.today().strftime("%Y-%m-%d")
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

                sites_to_add = []
                for s in parsed.get("extracted_sites", []):
                    sites_to_add.append({
                        "site_name": s.get("site_name", "公式・速報ページ"),
                        "url": s.get("url") or h["url"],
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": s.get("deadline_date", "公式発表確認"),
                        "status": "未応募"
                    })
                
                default_links = get_default_platform_links(prod_name, h["genre"])
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
                    current_sites = matched_item.get("sites") or []
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
        
        if new_count > 0:
            st.success(f"巡回完了：新たに {new_count} 件の商品を登録・更新しました！")
            st.rerun()
        else:
            st.info("巡回完了：既存の商品情報を最新状態に更新しました。")
            st.rerun()

with col_btn2:
    with st.popover("➕ 手動で案件を投入"):
        in_name = st.text_input("商品名")
        in_url = st.text_input("公式/速報URL")
        in_post = st.text_area("本文・告知文コピペ")
        in_genre = st.selectbox("ジャンル", ["TCG", "プレバン", "ソフビ", "スニーカー", "その他"])
        if st.button("登録する", use_container_width=True):
            if in_name or in_url or in_post:
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                parsed = analyze_master_intelligence(in_name or in_post[:25], in_url, in_genre, raw_text=in_post)
                prod_name = in_name if in_name else (parsed.get("standard_name") if parsed else in_post[:25])
                
                f_retail = int(parsed.get("retail_price", 0)) if parsed else 5000
                f_market = int(parsed.get("market_price", 0)) if parsed else 12000
                profit = f_market - int(f_market * 0.10) - 750 - f_retail
                margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                break_even = int((f_retail + 750) / 0.90)

                sites = []
                if in_url:
                    sites.append({
                        "site_name": "公式・指定URL",
                        "url": in_url,
                        "created_at": today_str,
                        "updated_at": today_str,
                        "deadline_date": "公式ページ参照",
                        "status": "未応募"
                    })
                for d in get_default_platform_links(prod_name, in_genre):
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

applying_count = 0
for it in items:
    sites = it.get("sites") or []
    if any(s.get("status") == "応募中" for s in sites):
        applying_count += 1

filter_applying = st.checkbox(f"【応募中】がある商品のみ表示（現在: {applying_count} 件）")

if not items:
    st.info("現在監視中の商品はありません。「全自動で最新情報を巡回収集」を実行してください。")
else:
    for item in items:
        raw_sites = item.get("sites") or []
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

            st.markdown(f"#### 📝 申込み・受付サイト一覧（{len(raw_sites)} 件）")

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
