import streamlit as st
import datetime
import urllib.parse
import json
import requests
from bs4 import BeautifulSoup
import feedparser
from google import genai
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

# --- 設定管理 (Secrets優先 / サイドバー入力可) ---
with st.sidebar:
    st.header("⚙️ システム設定")
    gemini_key = st.secrets.get("GEMINI_API_KEY", st.text_input("Gemini API Key", type="password"))
    sb_url = st.secrets.get("SUPABASE_URL", st.text_input("Supabase URL", placeholder="https://xxxx.supabase.co"))
    sb_key = st.secrets.get("SUPABASE_KEY", st.text_input("Supabase Anon Key", type="password"))
    discord_webhook = st.secrets.get("DISCORD_WEBHOOK_URL", st.text_input("Discord Webhook URL (通知用)", type="password"))

supabase: Client = None
if sb_url and sb_key:
    try:
        supabase = create_client(sb_url, sb_key)
    except Exception:
        pass

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
        except Exception:
            pass
    else:
        st.session_state.monitored_items.append(item)

def delete_from_db(item_id):
    if supabase:
        try:
            supabase.table("items").delete().eq("id", item_id).execute()
        except Exception:
            pass
    else:
        st.session_state.monitored_items = [x for x in st.session_state.monitored_items if x["id"] != item_id]

# --- 外部通知送信 ---
def send_discord_notify(webhook_url, item):
    if not webhook_url:
        return
    content = (
        f"🚨 **【高需要・定価自衛アラート検知】**\n"
        f"**商品名:** {item['name']}\n"
        f"**定価:** {item['retail_price']:,}円 ➔ **見込み利益:** +{item['profit']:,}円\n"
        f"**締切:** {item['deadline_date']} まで\n"
        f"**公式受付URL:** {item['url']}"
    )
    try:
        requests.post(webhook_url, json={"content": content}, timeout=5)
    except Exception:
        pass

# --- 自動巡回エンジン ---
def run_master_crawler():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    discovered = []

    # ① 墓場の画廊 (ソフビ)
    try:
        res = requests.get("https://store.hakabanogarou.jp/view/category/ct13", headers=headers, timeout=6)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for link in soup.select("a[href*='/view/item/']")[:3]:
                name = link.get_text(strip=True)
                url = link.get("href")
                if url and not url.startswith("http"):
                    url = f"https://store.hakabanogarou.jp{url}"
                if name and len(name) > 5:
                    discovered.append({"name": name, "url": url, "source": "墓場の画廊", "genre": "ソフビ・ホビー", "type": "公式巡回"})
    except Exception:
        pass

    # ② LivePocket (入店・購入抽選)
    try:
        lp_res = requests.get("https://t.livepocket.jp/event/search?word=%E6%8A%BD%E9%81%B8", headers=headers, timeout=6)
        if lp_res.status_code == 200:
            soup = BeautifulSoup(lp_res.text, "html.parser")
            for ev in soup.select("a[href*='/e/']")[:3]:
                t = ev.get_text(strip=True)
                h = ev.get("href")
                if h and not h.startswith("http"):
                    h = f"https://t.livepocket.jp{h}"
                if t and len(t) > 6:
                    discovered.append({"name": t, "url": h, "source": "LivePocket", "genre": "イベント抽選", "type": "公式巡回"})
    except Exception:
        pass

    # ③ プレミアムバンダイ (抽選販売)
    try:
        pb_res = requests.get("https://p-bandai.jp/lottery/", headers=headers, timeout=6)
        if pb_res.status_code == 200:
            soup = BeautifulSoup(pb_res.text, "html.parser")
            for a in soup.select("a[href*='/item/item-']")[:3]:
                t = a.get_text(strip=True)
                h = a.get("href")
                if h and not h.startswith("http"):
                    h = f"https://p-bandai.jp{h}"
                if t and len(t) > 6:
                    discovered.append({"name": t, "url": h, "source": "プレバン", "genre": "プレバン限定", "type": "公式巡回"})
    except Exception:
        pass

    # ④ X速報アカウントの自動吸い上げ (RSSフィード経由)
    x_rss_urls = [
        ("https://nitter.net/search/rss?q=%E6%8A%BD%E9%81%B8+%E3%83%AF%E3%83%B3%E3%83%94%E3%82%AB%E3%83%BC%E3%83%89", "TCG・トレカ"),
        ("https://nitter.net/search/rss?q=Supreme+Week", "Supreme・ストリート")
    ]
    for feed_url, genre in x_rss_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:2]:
                discovered.append({
                    "name": entry.title[:35],
                    "url": entry.link,
                    "source": "X速報フィード",
                    "genre": genre,
                    "raw_text": entry.summary,
                    "type": "X自動収集"
                })
        except Exception:
            pass

    return discovered

# --- AIディープリサーチ・相場判定・逆引き特定エンジン ---
def analyze_master_intelligence(name, url, genre, raw_text=""):
    if not gemini_key:
        return None

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    prompt = f"""
本日は {today_str} です。あなたは限定アイテム（ソフビ、ワンピカード、ポケカ、ドラゴンボールカード、Supreme、プレバン限定品）の専門アナリストです。
提供された情報から相場・定価・スケジュール・市場動向を推計し、必ず指定のJSON形式のみを出力してください。
もし情報がスニーカーやトレカ等の「商品名だけ」で公式URLが不明な場合は、公式定価販売元（Nike公式、プレバン、カドショ抽選等）の正規URLを逆引きで特定・推測してurlに格納してください。

【対象】
- 名称/タイトル: {name}
- URL: {url}
- ジャンル: {genre}
- 告知文: {raw_text}

【必須JSONフォーマット】
{{
  "name": "整理された商品名・イベント名",
  "url": "公式または正規抽選URL（不明なら推測URL）",
  "retail_price": 0,
  "market_price": 0,
  "deadline_date": "YYYY-MM-DD",
  "result_date": "YYYY-MM-DD",
  "difficulty": "★★★★☆ (激戦)" など,
  "market_trend": "段階的に上昇型（完全限定）" など
}}
"""
    try:
        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        clean = resp.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        if not data.get("url") or data.get("url") == "":
            data["url"] = url
        data["sns_genre"] = genre
        return data
    except Exception:
        return None

# ================= UIメイン =================
st.title("⚡ 自律巡回＆定価自衛ボード")

# 1. 自動巡回実行エリア
c_h1, c_h2 = st.columns([3, 2])
with c_h1:
    st.caption("スニダン高騰、X速報、プレバン、TCG、ソフビを全自動巡回")
with c_h2:
    if st.button("🔄 全自動マスター巡回", use_container_width=True):
        with st.spinner("全ソース（公式EC・X速報・スニダン）を横断スキャン中..."):
            hits = run_master_crawler()
            new_count = 0
            existing_urls = [x["url"] for x in load_db()]

            for h in hits:
                if h["url"] not in existing_urls:
                    parsed = analyze_master_intelligence(h["name"], h["url"], h["genre"], h.get("raw_text", ""))
                    if parsed:
                        f_retail = parsed["retail_price"]
                        f_market = parsed["market_price"]
                        fee = int(f_market * 0.10)
                        profit = f_market - fee - 750 - f_retail
                        margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                        break_even = int((f_retail + 750) / 0.90)

                        record = {
                            "id": str(datetime.datetime.now().timestamp() + new_count),
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
                        
                        # 激アツ案件ならDiscordへプッシュ通知
                        if profit >= 10000:
                            send_discord_notify(discord_webhook, record)

                        new_count += 1

            if new_count > 0:
                st.success(f"新たに {new_count} 件の激アツ案件を検知・保存しました！")
                st.rerun()
            else:
                st.info("巡回完了：新しい未登録案件はありませんでした。")

# 2. 手動・Xポスト緊急投入口
with st.expander("📥 Xポストや個別URLを手動で投入する", expanded=False):
    in_url = st.text_input("公式/告知URL", placeholder="https://...")
    in_post = st.text_area("Xのポスト文 または 告知本文（コピペ）", placeholder="【公式】ワンピースカード新弾 抽選受付開始...", height=80)
    in_genre = st.selectbox("ジャンル指定", ["ソフビ・ホビー", "TCG・トレカ", "Supreme・ストリート", "プレバン限定", "スニーカー"])
    
    if st.button("🪄 AI解析してリストに追加", use_container_width=True):
        if not in_url and not in_post:
            st.warning("URLかテキストのどちらかを入力してください。")
        else:
            with st.spinner("AIが相場・定価・スケジュールを高速解析中..."):
                t_name = in_post[:30] if in_post else "手動投入アイテム"
                parsed = analyze_master_intelligence(t_name, in_url, in_genre, raw_text=in_post)
                if parsed:
                    f_retail = parsed["retail_price"]
                    f_market = parsed["market_price"]
                    profit = f_market - int(f_market * 0.10) - 750 - f_retail
                    margin = round((profit / f_market) * 100, 1) if f_market > 0 else 0
                    break_even = int((f_retail + 750) / 0.90)

                    record = {
                        "id": str(datetime.datetime.now().timestamp()),
                        "name": parsed["name"],
                        "url": parsed["url"] if parsed["url"] else in_url,
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

# 3. 案件リスト ＆ ワンタップアクション
items = load_db()
st.subheader(f"📋 監視中案件（{len(items)} 件）")

if not items:
    st.info("監視中の案件はありません。「全自動マスター巡回」を実行してください。")
else:
    today_date = datetime.date.today()
    for item in items:
        d_date = datetime.date.fromisoformat(item["deadline_date"])
        days_left = (d_date - today_date).days
        date_status = f"⏳ あと {days_left} 日" if days_left > 0 else ("🚨 本日締切！" if days_left == 0 else "⛔ 終了")

        with st.container():
            c_top, c_del = st.columns([5, 1])
            with c_top:
                st.markdown(f"### {item['name']}")
                badge_class = "badge-auto" if "公式" in item.get("source_type", "") else "badge-x"
                st.markdown(f"<span class='{badge_class}'>{item.get('source_type', '自動検知')}</span> <span class='genre-badge'>{item.get('sns_genre', '限定')}</span> <span class='date-badge'>{date_status}</span>", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_{item['id']}"):
                    delete_from_db(item["id"])
                    st.rerun()

            p1, p2, p3 = st.columns(3)
            p1.caption("公式定価")
            p1.write(f"{item['retail_price']:,} 円")
            p2.caption("見込み差額")
            p2.markdown(f":green[**+{item['profit']:,} 円**]")
            p3.caption("損益分岐ライン")
            p3.write(f"⚠️ {item['break_even']:,} 円")

            st.caption(f"📊 難易度: {item['difficulty']} ｜ 📈 動向: {item['market_trend']}")

            # 相場確認ボタン
            kw = urllib.parse.quote(item["name"])
            m_url = f"https://jp.mercari.com/search?keyword={kw}"
            y_url = f"https://auctions.yahoo.co.jp/search/search?p={kw}"
            s_url = f"https://snkrdunk.com/search?keywords={kw}"

            st.caption("🔍 リアルタイム相場裏取り")
            sc1, sc2, sc3 = st.columns(3)
            sc1.link_button("スニダン", s_url, use_container_width=True)
            sc2.link_button("ヤフオク", y_url, use_container_width=True)
            sc3.link_button("メルカリ", m_url, use_container_width=True)

            # X向け「定価自衛」ガイド文生成
            tweet_text = (
                f"【定価購入の事前準備アラート】\n"
                f"■ {item['name']}\n"
                f"・応募締切: {d_date.strftime('%m/%d')} まで\n"
                f"・公式定価: {item['retail_price']:,}円\n\n"
                f"📊 定価入手の難易度: {item['difficulty']}\n"
                f"📈 流通・相場動向: {item['market_trend']}\n\n"
                f"💡 欲しいファンの方は二次流通で高騰する前に、必ず公式の事前エントリーを済ませておきましょう！\n\n"
                f"👇 公式受付リンク\n{item['url']}\n\n"
                f"#定価購入 #事前エントリー #自衛ガイド #{item.get('sns_genre', '限定品').replace('・', ' #')}"
            )
            x_url = f"https://twitter.com/intent/tweet?text={urllib.parse.quote(tweet_text)}"

            ac1, ac2 = st.columns(2)
            with ac1:
                st.link_button("🔗 公式受付ページ", item["url"], use_container_width=True)
            with ac2:
                st.link_button("📢 Xで自衛ガイドを投稿", x_url, use_container_width=True)

            st.caption(f"締切: {item['deadline_date']} ｜ 発表: {item['result_date']}")
            st.markdown("---")
