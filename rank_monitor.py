# -*- coding: utf-8 -*-

import os
import re
import requests
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

from config import (
    REGIONS,
    IOS_CHARTS,
    ANDROID_CHARTS,
    WATCH_APPS,
    TOP_N,
    ALERT_RISE_THRESHOLD,
    ALERT_DROP_THRESHOLD,
    NEW_ENTRY_ALERT_RANK,
    FEISHU_WEBHOOK,
)


TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "rank_history.csv")
os.makedirs(DATA_DIR, exist_ok=True)


def get_feishu_webhook():
    return os.getenv("FEISHU_WEBHOOK") or FEISHU_WEBHOOK


def fetch_ios_chart(region, chart_type, limit=200):
    chart_map = {
        "top-free": "topfreeapplications",
        "top-grossing": "topgrossingapplications",
    }

    rss_type = chart_map.get(chart_type)
    if not rss_type:
        return []

    url = f"https://itunes.apple.com/{region}/rss/{rss_type}/limit={limit}/genre=6014/json"

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        entries = data.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        rows = []

        for idx, item in enumerate(entries, start=1):
            rows.append({
                "date": TODAY,
                "platform": "ios",
                "region": region,
                "region_name": REGIONS.get(region, region),
                "chart_type": chart_type,
                "rank": idx,
                "app_name": item.get("im:name", {}).get("label", ""),
                "app_id": str(item.get("id", {}).get("attributes", {}).get("im:id", "")),
                "developer": item.get("im:artist", {}).get("label", ""),
                "url": item.get("id", {}).get("label", ""),
            })

        print(f"[OK] iOS {region} {chart_type}: {len(rows)}")
        return rows

    except Exception as e:
        print(f"[ERROR] iOS {region} {chart_type}: {e}")
        return []


def fetch_android_chart(region, chart_type, limit=200):
    chart_map = {
        "free": f"https://www.appbrain.com/stats/google-play-rankings/top_free/game/{region}",
        "grossing": f"https://www.appbrain.com/stats/google-play-rankings/top_grossing/game/{region}",
    }

    url = chart_map.get(chart_type)
    if not url:
        return []

    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }

        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        rows = []
        seen = set()

        for link in soup.select("a[href*='/app/']"):
            href = link.get("href", "")
            text = link.get_text(strip=True)

            if not text:
                continue

            package_name = href.split("/")[-1].strip()

            if not package_name or package_name in seen:
                continue

            seen.add(package_name)

            rows.append({
                "date": TODAY,
                "platform": "android",
                "region": region,
                "region_name": REGIONS.get(region, region),
                "chart_type": chart_type,
                "rank": len(rows) + 1,
                "app_name": text,
                "app_id": package_name,
                "developer": "",
                "url": "https://www.appbrain.com" + href,
            })

            if len(rows) >= limit:
                break

        print(f"[OK] Android {region} {chart_type}: {len(rows)}")
        return rows

    except Exception as e:
        print(f"[ERROR] Android {region} {chart_type}: {e}")
        return []


def save_rows(rows):
    if not rows:
        print("[WARN] 无数据可保存")
        return

    df = pd.DataFrame(rows)

    if os.path.exists(HISTORY_FILE):
        old = pd.read_csv(HISTORY_FILE)
        new_df = pd.concat([old, df], ignore_index=True)
        new_df = new_df.drop_duplicates(
            subset=["date", "platform", "region", "chart_type", "app_id"],
            keep="last"
        )
    else:
        new_df = df

    new_df.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    print(f"[OK] 数据已保存：{HISTORY_FILE}")


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    return pd.read_csv(HISTORY_FILE)


def has_previous_history(history):
    if history.empty:
        return False

    old = history[history["date"] < TODAY]
    return not old.empty


def get_previous_rank(history, platform, region, chart_type, app_id):
    if history.empty:
        return None

    sub = history[
        (history["platform"] == platform) &
        (history["region"] == region) &
        (history["chart_type"] == chart_type) &
        (history["app_id"].astype(str) == str(app_id)) &
        (history["date"] < TODAY)
    ]

    if sub.empty:
        return None

    latest_date = sub["date"].max()
    latest = sub[sub["date"] == latest_date]

    if latest.empty:
        return None

    return int(latest.iloc[0]["rank"])


def format_change(today_rank, previous_rank):
    if previous_rank is None:
        return "新入榜"

    diff = previous_rank - today_rank

    if diff > 0:
        return f"↑{diff}"
    if diff < 0:
        return f"↓{abs(diff)}"
    return "→"


def change_value(today_rank, previous_rank):
    if previous_rank is None:
        return None
    return previous_rank - today_rank


def get_chart_name(platform, chart_type):
    if platform == "ios":
        return IOS_CHARTS.get(chart_type, chart_type)
    return ANDROID_CHARTS.get(chart_type, chart_type)


def match_keyword_exact_or_contains(app_name, keyword):
    app_name = str(app_name).strip()
    keyword = str(keyword).strip()

    if not app_name or not keyword:
        return False

    if app_name == keyword:
        return True

    return keyword.lower() in app_name.lower()


def match_watch_app(today_df, watch):
    matched_parts = []

    apple_ids = [str(x) for x in watch.get("apple_ids", []) if str(x).strip()]
    google_packages = [str(x) for x in watch.get("google_packages", []) if str(x).strip()]
    keywords = [str(x) for x in watch.get("keywords", []) if str(x).strip()]

    if apple_ids:
        matched_parts.append(
            today_df[
                (today_df["platform"] == "ios") &
                (today_df["app_id"].astype(str).isin(apple_ids))
            ]
        )

    if google_packages:
        matched_parts.append(
            today_df[
                (today_df["platform"] == "android") &
                (today_df["app_id"].astype(str).isin(google_packages))
            ]
        )

    for keyword in keywords:
        mask = today_df["app_name"].apply(
            lambda x: match_keyword_exact_or_contains(x, keyword)
        )
        matched_parts.append(today_df[mask])

    if not matched_parts:
        return pd.DataFrame()

    matched = pd.concat(matched_parts, ignore_index=True)
    matched = matched.drop_duplicates(
        subset=["platform", "region", "chart_type", "app_id"]
    )

    return matched


def build_top_section(lines, today_df, history):
    for region, region_name in REGIONS.items():
        lines.append(f"========== {region_name} ==========")

        for platform, chart_map in [
            ("ios", IOS_CHARTS),
            ("android", ANDROID_CHARTS),
        ]:
            for chart_type, chart_name in chart_map.items():
                sub = today_df[
                    (today_df["platform"] == platform) &
                    (today_df["region"] == region) &
                    (today_df["chart_type"] == chart_type)
                ].sort_values("rank")

                lines.append(f"\n【{chart_name} TOP{TOP_N}】")

                if sub.empty:
                    lines.append("暂无数据")
                    continue

                for _, row in sub.head(TOP_N).iterrows():
                    previous_rank = get_previous_rank(
                        history,
                        row["platform"],
                        row["region"],
                        row["chart_type"],
                        row["app_id"]
                    )
                    change = format_change(int(row["rank"]), previous_rank)
                    lines.append(f"{int(row['rank'])}. {row['app_name']} {change}")

        lines.append("")


def build_watch_section(lines, today_df, history):
    lines.append("========== 重点产品监控 ==========")

    has_watch_result = False

    for watch in WATCH_APPS:
        matched = match_watch_app(today_df, watch)

        if matched.empty:
            continue

        has_watch_result = True
        lines.append(f"\n【{watch['name']}】")

        for _, row in matched.sort_values(["region", "platform", "chart_type", "rank"]).iterrows():
            previous_rank = get_previous_rank(
                history,
                row["platform"],
                row["region"],
                row["chart_type"],
                row["app_id"]
            )
            change = format_change(int(row["rank"]), previous_rank)

            platform_name = "iOS" if row["platform"] == "ios" else "Google"
            chart_name = get_chart_name(row["platform"], row["chart_type"])

            lines.append(
                f"{row['region_name']}｜{platform_name}｜{chart_name}："
                f"{int(row['rank'])}（{change}）"
            )

    if not has_watch_result:
        lines.append("今日重点产品未进入已抓取榜单范围。")


def build_alert_section(lines, today_df, history):
    lines.append("")
    lines.append("========== 榜单异动预警 ==========")

    if not has_previous_history(history):
        lines.append("暂无历史数据，今日仅建立基准，明日起开始预警。")
        return

    alerts = []

    for _, row in today_df.iterrows():
        previous_rank = get_previous_rank(
            history,
            row["platform"],
            row["region"],
            row["chart_type"],
            row["app_id"]
        )

        diff = change_value(int(row["rank"]), previous_rank)

        if diff is None:
            if int(row["rank"]) <= NEW_ENTRY_ALERT_RANK:
                alerts.append(
                    f"🆕 新进榜TOP{NEW_ENTRY_ALERT_RANK}｜{row['region_name']}｜{get_chart_name(row['platform'], row['chart_type'])}｜"
                    f"{int(row['rank'])}. {row['app_name']}"
                )
            continue

        if diff >= ALERT_RISE_THRESHOLD:
            alerts.append(
                f"🔥 大幅上涨｜{row['region_name']}｜{get_chart_name(row['platform'], row['chart_type'])}｜"
                f"{row['app_name']}：{int(row['rank'])}（↑{diff}）"
            )

        if diff <= -ALERT_DROP_THRESHOLD:
            alerts.append(
                f"⚠️ 大幅下跌｜{row['region_name']}｜{get_chart_name(row['platform'], row['chart_type'])}｜"
                f"{row['app_name']}：{int(row['rank'])}（↓{abs(diff)}）"
            )

    if not alerts:
        lines.append("暂无明显异动。")
    else:
        for item in alerts[:30]:
            lines.append(item)


def build_report(today_rows):
    history = load_history()
    today_df = pd.DataFrame(today_rows)

    lines = []
    lines.append("【台湾手游榜单监控日报 V2.1】")
    lines.append(f"日期：{TODAY}")
    lines.append("")

    if today_df.empty:
        lines.append("今日未抓取到榜单数据，请检查 GitHub Actions 日志。")
        return "\n".join(lines)

    build_top_section(lines, today_df, history)
    build_watch_section(lines, today_df, history)
    build_alert_section(lines, today_df, history)

    return "\n".join(lines)


def send_feishu(text):
    webhook = get_feishu_webhook()

    if not webhook:
        print("[WARN] 未配置 FEISHU_WEBHOOK")
        print(text)
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        resp.raise_for_status()
        print("[OK] 飞书推送成功")
    except Exception as e:
        print(f"[ERROR] 飞书推送失败: {e}")
        print(text)


def main():
    all_rows = []

    for region in REGIONS.keys():
        for chart_type in IOS_CHARTS.keys():
            all_rows.extend(fetch_ios_chart(region, chart_type))

        for chart_type in ANDROID_CHARTS.keys():
            all_rows.extend(fetch_android_chart(region, chart_type))

    print(f"TOTAL ROWS: {len(all_rows)}")

    save_rows(all_rows)

    report = build_report(all_rows)

    report_path = os.path.join(DATA_DIR, f"daily_report_{TODAY}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    send_feishu(report)


if __name__ == "__main__":
    main()
