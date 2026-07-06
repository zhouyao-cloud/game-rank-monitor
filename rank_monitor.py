# -*- coding: utf-8 -*-

import os
import re
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import (
    REGIONS,
    IOS_CHARTS,
    ANDROID_CHARTS,
    WATCH_APPS,
    TOP_N,
    TREND_DAYS,
    ALERT_RISE_THRESHOLD,
    ALERT_DROP_THRESHOLD,
    NEW_ENTRY_ALERT_RANK,
    FEISHU_WEBHOOK,
)

TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_DIR = "data"
CHART_DIR = os.path.join(DATA_DIR, "charts")
HISTORY_FILE = os.path.join(DATA_DIR, "rank_history.csv")

CHART_RANK_LIMITS = {
    ("ios", "top-free"): 200,
    ("ios", "top-grossing"): 200,
    ("android", "free"): 100,
    ("android", "grossing"): 100,
}

KEY_CHARTS = {
    ("ios", "top-grossing"),
    ("android", "grossing"),
}

SUMMARY_ALERT_LIMIT = 5
ALERT_DISPLAY_LIMIT = 30
ANDROID_NON_GAME_KEYWORDS = [
    "all email",
    "app dual space",
    "claim - make them pay",
    "dual cloner",
    "emailcenter",
    "funny videos",
    "gamecloner",
    "megalol",
    "on-demand",
    "rideco",
    "russellinvestments",
    "super tracker",
]

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)


def get_feishu_webhook():
    return os.getenv("FEISHU_WEBHOOK") or FEISHU_WEBHOOK


def get_github_base_url():
    repo = os.getenv("GITHUB_REPOSITORY", "")
    branch = os.getenv("GITHUB_REF_NAME", "main")
    if not repo:
        return ""
    return f"https://github.com/{repo}/blob/{branch}"


def to_github_file_url(file_path):
    base_url = get_github_base_url()
    if not base_url:
        return ""
    normalized_path = file_path.replace(os.sep, "/")
    return f"{base_url}/{normalized_path}"


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
        entries = resp.json().get("feed", {}).get("entry", [])
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


def fetch_android_chart(region, chart_type, limit=100):
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
        containers = soup.select("table, ol, ul, div")
        ranked_containers = [
            container for container in containers
            if len(container.select("a[href^='/app/']")) >= min(limit, 20)
        ]
        search_roots = ranked_containers or [soup]

        for root in search_roots:
            for link in root.select("a[href^='/app/']"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if not text or text.startswith("View "):
                    continue

                package_name = href.split("?")[0].rstrip("/").split("/")[-1].strip()
                if not re.match(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$", package_name):
                    continue
                if not is_android_game_candidate(text, package_name):
                    continue
                if package_name in seen:
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
            if len(rows) >= limit:
                break

        print(f"[OK] Android {region} {chart_type}: {len(rows)}")
        return rows
    except Exception as e:
        print(f"[ERROR] Android {region} {chart_type}: {e}")
        return []


def is_android_game_candidate(app_name, package_name):
    value = f"{app_name} {package_name}".lower()
    return not any(keyword in value for keyword in ANDROID_NON_GAME_KEYWORDS)


def get_chart_rank_limit(platform, chart_type):
    return CHART_RANK_LIMITS.get((platform, chart_type), 200)


def clean_rank_rows(df):
    if df.empty:
        return df

    cleaned = df.copy()
    cleaned["_row_order"] = range(len(cleaned))
    cleaned["rank"] = pd.to_numeric(cleaned["rank"], errors="coerce")
    cleaned = cleaned.dropna(subset=["rank", "app_id"])
    cleaned["rank"] = cleaned["rank"].astype(int)
    cleaned = cleaned[cleaned["rank"] > 0]

    parts = []
    group_cols = ["date", "platform", "region", "chart_type"]
    for group_key, group in cleaned.groupby(group_cols, sort=False):
        _, platform, _, chart_type = group_key
        limit = get_chart_rank_limit(platform, chart_type)
        group = group[group["rank"] <= limit]
        group = group.sort_values(["rank", "_row_order"])
        group = group.drop_duplicates(subset=["rank"], keep="last")
        group = group.drop_duplicates(subset=["app_id"], keep="first")
        parts.append(group.sort_values("rank").head(limit))

    if not parts:
        return cleaned.drop(columns=["_row_order"]).iloc[0:0]

    return pd.concat(parts, ignore_index=True).drop(columns=["_row_order"])


def save_rows(rows):
    if not rows:
        print("[WARN] 无数据可保存")
        return

    df = clean_rank_rows(pd.DataFrame(rows))
    if os.path.exists(HISTORY_FILE):
        old = pd.read_csv(HISTORY_FILE)
        new_df = pd.concat([old, df], ignore_index=True)
        new_df = new_df.drop_duplicates(
            subset=["date", "platform", "region", "chart_type", "app_id"],
            keep="last",
        )
        new_df = clean_rank_rows(new_df)
    else:
        new_df = df

    new_df.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    print(f"[OK] 数据已保存：{HISTORY_FILE}")


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    return pd.read_csv(HISTORY_FILE)


def has_previous_history(history):
    return not history.empty and not history[history["date"] < TODAY].empty


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

    latest = sub[sub["date"] == sub["date"].max()]
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


def is_key_chart(platform, chart_type):
    return (platform, chart_type) in KEY_CHARTS


def match_keyword_exact_or_contains(app_name, keyword):
    app_name = str(app_name).strip()
    keyword = str(keyword).strip()
    if not app_name or not keyword:
        return False
    return app_name == keyword or keyword.lower() in app_name.lower()


def match_watch_app(df, watch):
    matched_parts = []
    apple_ids = [str(x) for x in watch.get("apple_ids", []) if str(x).strip()]
    google_packages = [str(x) for x in watch.get("google_packages", []) if str(x).strip()]
    keywords = [str(x) for x in watch.get("keywords", []) if str(x).strip()]

    if apple_ids:
        matched_parts.append(df[(df["platform"] == "ios") & (df["app_id"].astype(str).isin(apple_ids))])
    if google_packages:
        matched_parts.append(df[(df["platform"] == "android") & (df["app_id"].astype(str).isin(google_packages))])
    for keyword in keywords:
        matched_parts.append(df[df["app_name"].apply(lambda x: match_keyword_exact_or_contains(x, keyword))])

    if not matched_parts:
        return pd.DataFrame()

    matched = pd.concat(matched_parts, ignore_index=True)
    return matched.drop_duplicates(subset=["platform", "region", "chart_type", "app_id"])


def row_identity(row):
    return (
        row["platform"],
        row["region"],
        row["chart_type"],
        str(row["app_id"]),
    )


def build_watch_lookup(today_df):
    watch_lookup = {}
    for watch in WATCH_APPS:
        matched = match_watch_app(today_df, watch)
        for _, watch_row in matched.iterrows():
            watch_lookup[row_identity(watch_row)] = watch["name"]
    return watch_lookup


def get_rank_series(history, row, days=TREND_DAYS):
    if history.empty:
        return pd.DataFrame()

    start_date = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    sub = history[
        (history["platform"] == row["platform"]) &
        (history["region"] == row["region"]) &
        (history["chart_type"] == row["chart_type"]) &
        (history["app_id"].astype(str) == str(row["app_id"])) &
        (history["date"] >= start_date)
    ].copy()

    if sub.empty:
        return sub

    sub["rank"] = pd.to_numeric(sub["rank"], errors="coerce")
    sub = sub.dropna(subset=["rank"])
    sub["rank"] = sub["rank"].astype(int)
    return sub.sort_values("date")


def format_trend_note(history, row):
    series = get_rank_series(history, row, days=TREND_DAYS)
    if len(series) < 2:
        return ""

    notes = []
    recent = series.tail(3)
    if len(recent) == 3:
        ranks = recent["rank"].tolist()
        if ranks[0] > ranks[1] > ranks[2]:
            notes.append("近3日连续上升")
        elif ranks[0] < ranks[1] < ranks[2]:
            notes.append("近3日连续下滑")

    today_rank = int(series.iloc[-1]["rank"])
    if len(series) >= 3:
        best_rank = int(series["rank"].min())
        worst_rank = int(series["rank"].max())
        if today_rank == best_rank:
            notes.append(f"近{TREND_DAYS}日新高")
        elif today_rank == worst_rank:
            notes.append(f"近{TREND_DAYS}日新低")

    return "；".join(notes[:2])


def classify_alert(rank, diff, is_watch_app, is_key_chart_value):
    if diff is None:
        if is_watch_app and rank <= NEW_ENTRY_ALERT_RANK:
            return "P1"
        if rank <= 10 and is_key_chart_value:
            return "P1"
        return "P2"

    magnitude = abs(diff)
    if is_watch_app and magnitude >= 10:
        return "P0"
    if is_watch_app and magnitude >= 5:
        return "P1"
    if is_key_chart_value and rank <= 20 and magnitude >= 15:
        return "P1"
    if rank <= 10 and magnitude >= 10:
        return "P1"
    return "P2"


def collect_alerts(today_df, history):
    if not has_previous_history(history):
        return []

    watch_lookup = build_watch_lookup(today_df)
    alerts = []
    for _, row in today_df.iterrows():
        rank = int(row["rank"])
        previous_rank = get_previous_rank(
            history,
            row["platform"],
            row["region"],
            row["chart_type"],
            row["app_id"],
        )
        diff = change_value(rank, previous_rank)

        if diff is None and rank > NEW_ENTRY_ALERT_RANK:
            continue
        if diff is not None and abs(diff) < min(ALERT_RISE_THRESHOLD, ALERT_DROP_THRESHOLD):
            continue

        chart_name = get_chart_name(row["platform"], row["chart_type"])
        watch_name = watch_lookup.get(row_identity(row))
        is_watch_app = bool(watch_name)
        is_key_chart_value = is_key_chart(row["platform"], row["chart_type"])
        priority = classify_alert(rank, diff, is_watch_app, is_key_chart_value)

        if diff is None:
            text = f"🆕 新进榜TOP{NEW_ENTRY_ALERT_RANK}｜{row['region_name']}｜{chart_name}｜{rank}. {row['app_name']}"
            magnitude = NEW_ENTRY_ALERT_RANK - rank + 1
        elif diff > 0:
            text = f"🔥 大幅上涨｜{row['region_name']}｜{chart_name}｜{row['app_name']}：{rank}（↑{diff}）"
            magnitude = abs(diff)
        else:
            text = f"⚠️ 大幅下跌｜{row['region_name']}｜{chart_name}｜{row['app_name']}：{rank}（↓{abs(diff)}）"
            magnitude = abs(diff)

        alerts.append({
            "text": text,
            "priority": priority,
            "watch_name": watch_name,
            "is_watch_app": is_watch_app,
            "is_key_chart": is_key_chart_value,
            "magnitude": magnitude,
            "rank": rank,
        })

    return sorted(
        alerts,
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2}.get(item["priority"], 3),
            0 if item["is_watch_app"] else 1,
            -item["magnitude"],
            item["rank"],
        ),
    )


def build_business_summary(lines, today_df, history):
    lines.append("========== 今日业务摘要 ==========")
    if today_df.empty:
        lines.append("今日未抓取到榜单数据，暂无法判断业务异动。")
        lines.append("")
        return

    alerts = collect_alerts(today_df, history)
    p0_alerts = [item for item in alerts if item["priority"] == "P0"]
    p1_alerts = [item for item in alerts if item["priority"] == "P1"]

    if not has_previous_history(history):
        lines.append("今日主要用于建立历史基准，明日起可输出涨跌和连续趋势判断。")
    elif p0_alerts:
        lines.append(f"重点风险/机会：发现 {len(p0_alerts)} 条 P0 级重点异动，需要优先关注。")
    elif p1_alerts:
        lines.append(f"重点风险/机会：发现 {len(p1_alerts)} 条 P1 级异动，建议关注是否由活动或投放导致。")
    else:
        lines.append("重点风险/机会：暂无高优先级异动，整体波动处于常规范围。")

    watch_highlights = []
    for watch in WATCH_APPS:
        matched = match_watch_app(today_df, watch)
        if matched.empty:
            continue

        matched = matched.copy()
        matched["is_key_chart"] = matched.apply(
            lambda row: is_key_chart(row["platform"], row["chart_type"]),
            axis=1,
        )
        matched = matched.sort_values(
            ["is_key_chart", "platform", "rank"],
            ascending=[False, True, True],
        )

        row = matched.iloc[0]
        previous_rank = get_previous_rank(
            history,
            row["platform"],
            row["region"],
            row["chart_type"],
            row["app_id"],
        )
        change = format_change(int(row["rank"]), previous_rank)
        trend_note = format_trend_note(history, row)
        chart_name = get_chart_name(row["platform"], row["chart_type"])
        suffix = f"，{trend_note}" if trend_note else ""
        watch_highlights.append(
            f"{watch['name']}：{chart_name}第{int(row['rank'])}（{change}{suffix}）"
        )

    if watch_highlights:
        lines.append("重点产品：" + "；".join(watch_highlights[:4]))
        if len(watch_highlights) > 4:
            lines.append(f"另有 {len(watch_highlights) - 4} 个重点产品在下方详情中展示。")
    else:
        lines.append("重点产品：今日重点产品未进入已抓取榜单范围。")

    key_alerts = p0_alerts + p1_alerts
    if key_alerts:
        lines.append("优先查看：")
        for item in key_alerts[:SUMMARY_ALERT_LIMIT]:
            lines.append(f"{item['priority']}｜{item['text']}")
    else:
        lines.append("优先查看：暂无 P0/P1 级预警。")

    lines.append("")


def build_top_section(lines, today_df, history):
    for region, region_name in REGIONS.items():
        lines.append(f"========== {region_name} ==========")
        for platform, chart_map in [("ios", IOS_CHARTS), ("android", ANDROID_CHARTS)]:
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
                        row["app_id"],
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
                row["app_id"],
            )
            change = format_change(int(row["rank"]), previous_rank)
            platform_name = "iOS" if row["platform"] == "ios" else "Google"
            chart_name = get_chart_name(row["platform"], row["chart_type"])
            trend_note = format_trend_note(history, row)
            trend_text = f"；{trend_note}" if trend_note else ""
            lines.append(f"{row['region_name']}｜{platform_name}｜{chart_name}：{int(row['rank'])}（{change}{trend_text}）")

    if not has_watch_result:
        lines.append("今日重点产品未进入已抓取榜单范围。")


def build_alert_section(lines, today_df, history):
    lines.append("")
    lines.append("========== 榜单异动预警 ==========")

    if not has_previous_history(history):
        lines.append("暂无历史数据，今日仅建立基准，明日起开始预警。")
        lines.append("")
        return

    alerts = collect_alerts(today_df, history)
    if not alerts:
        lines.append("暂无明显异动。")
        lines.append("")
        return

    shown = 0
    for priority, title in [
        ("P0", "P0｜重点产品大幅波动"),
        ("P1", "P1｜重点关注异动"),
        ("P2", "P2｜普通榜单异动"),
    ]:
        priority_alerts = [item for item in alerts if item["priority"] == priority]
        if not priority_alerts:
            continue

        lines.append(f"\n【{title}】")
        for item in priority_alerts:
            if shown >= ALERT_DISPLAY_LIMIT:
                break
            lines.append(item["text"])
            shown += 1

        if shown >= ALERT_DISPLAY_LIMIT:
            break

    if len(alerts) > shown:
        lines.append(f"另有 {len(alerts) - shown} 条异动未展示。")

    lines.append("")


def generate_trend_charts(history):
    if history.empty:
        return []

    chart_infos = []
    start_date = (datetime.now() - timedelta(days=TREND_DAYS - 1)).strftime("%Y-%m-%d")

    for watch in WATCH_APPS:
        app_history = match_watch_app(history, watch)
        if app_history.empty:
            continue

        app_history = app_history[app_history["date"] >= start_date]
        if app_history.empty:
            continue

        for region in sorted(app_history["region"].dropna().unique()):
            region_history = app_history[app_history["region"] == region]
            region_name = REGIONS.get(region, region)
            for platform in ["ios", "android"]:
                chart_types = ["top-grossing"] if platform == "ios" else ["grossing"]
                for chart_type in chart_types:
                    sub = region_history[
                        (region_history["platform"] == platform) &
                        (region_history["chart_type"] == chart_type)
                    ].copy()
                    if sub.empty:
                        continue

                    sub = sub.sort_values("date")
                    sub["rank"] = sub["rank"].astype(int)
                    chart_title = f"{watch['name']} - {region_name} - {get_chart_name(platform, chart_type)} - 近{TREND_DAYS}日"
                    safe_name = re.sub(r"[^\w\u4e00-\u9fff]+", "_", watch["name"])
                    file_name = f"{TODAY}_{region}_{safe_name}_{platform}_{chart_type}.png"
                    file_path = os.path.join(CHART_DIR, file_name)

                    plt.figure(figsize=(10, 5))
                    plt.plot(sub["date"], sub["rank"], marker="o")
                    plt.gca().invert_yaxis()
                    plt.title(chart_title)
                    plt.xlabel("日期")
                    plt.ylabel("排名")
                    plt.xticks(rotation=45)
                    plt.grid(True, linestyle="--", alpha=0.4)
                    plt.tight_layout()
                    plt.savefig(file_path, dpi=160)
                    plt.close()

                    chart_infos.append({
                        "watch_name": watch["name"],
                        "region": region,
                        "region_name": region_name,
                        "platform": platform,
                        "chart_type": chart_type,
                        "chart_name": get_chart_name(platform, chart_type),
                        "file_path": file_path,
                        "github_url": to_github_file_url(file_path),
                    })

    print(f"[OK] 趋势图生成数量：{len(chart_infos)}")
    return chart_infos


def build_trend_section(lines, chart_infos):
    lines.append("")
    lines.append("========== 重点产品趋势图 ==========")

    if not chart_infos:
        lines.append("暂无足够历史数据生成趋势图。")
        return

    lines.append(f"已生成 {len(chart_infos)} 张趋势图：")
    for idx, item in enumerate(chart_infos[:20], start=1):
        target = item["github_url"] or item["file_path"]
        lines.append(f"{idx}. {item['watch_name']}｜{item['region_name']}｜{item['chart_name']}：{target}")


def send_feishu_text(text):
    webhook = get_feishu_webhook()
    if not webhook:
        print("[WARN] 未配置 FEISHU_WEBHOOK")
        print(text)
        return

    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        resp.raise_for_status()
        print("[OK] 飞书文本推送成功")
    except Exception as e:
        print(f"[ERROR] 飞书文本推送失败: {e}")
        print(text)


def build_report(today_rows):
    history = load_history()
    today_df = pd.DataFrame(today_rows)

    lines = ["【台湾手游榜单监控日报 V2.5】", f"日期：{TODAY}", ""]
    if today_df.empty:
        lines.append("今日未抓取到榜单数据，请检查 GitHub Actions 日志。")
        return "\n".join(lines)

    build_business_summary(lines, today_df, history)
    build_watch_section(lines, today_df, history)
    build_alert_section(lines, today_df, history)
    build_top_section(lines, today_df, history)
    build_trend_section(lines, generate_trend_charts(history))
    return "\n".join(lines)


def main():
    all_rows = []
    for region in REGIONS.keys():
        for chart_type in IOS_CHARTS.keys():
            all_rows.extend(fetch_ios_chart(region, chart_type))
        for chart_type in ANDROID_CHARTS.keys():
            all_rows.extend(fetch_android_chart(region, chart_type))

    all_rows = clean_rank_rows(pd.DataFrame(all_rows)).to_dict("records")
    print(f"TOTAL ROWS: {len(all_rows)}")

    save_rows(all_rows)
    report = build_report(all_rows)

    report_path = os.path.join(DATA_DIR, f"daily_report_{TODAY}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    send_feishu_text(report)


if __name__ == "__main__":
    main()
