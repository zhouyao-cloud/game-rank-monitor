# -*- coding: utf-8 -*-

import os
import requests
import pandas as pd
from datetime import datetime

from config import REGIONS, IOS_CHARTS, ANDROID_CHARTS, WATCH_LIST, FEISHU_WEBHOOK


TODAY = datetime.now().strftime("%Y-%m-%d")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


def get_feishu_webhook():
    return os.getenv("FEISHU_WEBHOOK") or FEISHU_WEBHOOK


def fetch_ios_chart(region, chart_type, limit=200):
    url = f"https://rss.applemarketingtools.com/api/v2/{region}/apps/{chart_type}/{limit}/apps.json"

    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("feed", {}).get("results", [])

        rows = []
        for idx, item in enumerate(results, start=1):
            rows.append({
                "date": TODAY,
                "platform": "ios",
                "region": region,
                "region_name": REGIONS.get(region, region),
                "chart_type": chart_type,
                "rank": idx,
                "app_name": item.get("name", ""),
                "app_id": item.get("id", ""),
                "developer": item.get("artistName", ""),
                "url": item.get("url", ""),
            })

        print(f"[OK] iOS {region} {chart_type}: {len(rows)}")
        return rows

    except Exception as e:
        print(f"[ERROR] iOS {region} {chart_type}: {e}")
        return []


def fetch_android_chart(region, chart_type, limit=200):
    """
    V1.0 暂时跳过 Google Play 榜单。
    先确保 iOS 榜单 + 飞书推送 + 历史数据保存跑通。
    """
    print(f"[SKIP] Android {region} {chart_type}: V1暂不抓取")
    return []


def save_rows(rows):
    if not rows:
        print("[WARN] 无数据可保存")
        return

    df = pd.DataFrame(rows)
    file_path = os.path.join(DATA_DIR, "rank_history.csv")

    if os.path.exists(file_path):
        old = pd.read_csv(file_path)
        new_df = pd.concat([old, df], ignore_index=True)
        new_df = new_df.drop_duplicates(
            subset=["date", "platform", "region", "chart_type", "app_id"],
            keep="last"
        )
    else:
        new_df = df

    new_df.to_csv(file_path, index=False, encoding="utf-8-sig")
    print(f"[OK] 数据已保存：{file_path}")


def load_history():
    file_path = os.path.join(DATA_DIR, "rank_history.csv")
    if not os.path.exists(file_path):
        return pd.DataFrame()

    return pd.read_csv(file_path)


def get_previous_rank(df, platform, region, chart_type, app_id):
    if df.empty:
        return None

    sub = df[
        (df["platform"] == platform) &
        (df["region"] == region) &
        (df["chart_type"] == chart_type) &
        (df["app_id"].astype(str) == str(app_id)) &
        (df["date"] < TODAY)
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
    elif diff < 0:
        return f"↓{abs(diff)}"
    else:
        return "→"


def get_chart_name(platform, chart_type):
    if platform == "ios":
        return IOS_CHARTS.get(chart_type, chart_type)
    return ANDROID_CHARTS.get(chart_type, chart_type)


def build_report(today_rows):
    history = load_history()
    today_df = pd.DataFrame(today_rows)

    lines = []
    lines.append("【港澳台手游榜单日报】")
    lines.append(f"日期：{TODAY}")
    lines.append("")

    if today_df.empty:
        lines.append("今日未抓取到榜单数据，请检查 GitHub Actions 日志。")
        return "\n".join(lines)

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

                lines.append(f"\n【{chart_name} TOP10】")

                if sub.empty:
                    lines.append("暂无数据")
                    continue

                for _, row in sub.head(10).iterrows():
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

    lines.append("========== 重点产品监控 ==========")

    has_watch_result = False

    for watch in WATCH_LIST:
        matched = today_df[
            today_df["app_name"].astype(str).str.contains(watch, case=False, na=False)
        ]

        if matched.empty:
            continue

        has_watch_result = True
        lines.append(f"\n【{watch}】")

        for _, row in matched.sort_values(["region", "platform", "chart_type"]).iterrows():
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
        resp = requests.post(webhook, json=payload, timeout=20)
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

    save_rows(all_rows)

    report = build_report(all_rows)

    report_path = os.path.join(DATA_DIR, f"daily_report_{TODAY}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    send_feishu(report)


if __name__ == "__main__":
    main()
