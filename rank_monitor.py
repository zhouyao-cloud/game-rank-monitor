# -*- coding: utf-8 -*-

import os
import re
import json
import requests
import pandas as pd
import matplotlib.pyplot as plt

from datetime import datetime, timedelta
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
PRE_REGISTRATION_HISTORY_FILE = os.path.join(DATA_DIR, "pre_registration_history.csv")

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
PRE_REGISTRATION_DISPLAY_LIMIT = 20
PRE_REGISTRATION_DETAIL_LIMIT = 30
APP_STORE_PRE_ORDER_DETAIL_LIMIT = 20

PRE_REGISTRATION_SOURCES = [
    {
        "name": "Google Play预注册游戏",
        "platform": "android",
        "url": "https://play.google.com/store/apps/collection/promotion_3000000d51_pre_registration_games?gl=TW&hl=zh_TW",
    },
    {
        "name": "Google Play游戏首页预注册",
        "platform": "android",
        "url": "https://play.google.com/store/games?gl=TW&hl=zh_TW",
    },
]

APP_STORE_PRE_ORDER_SOURCES = [
    {
        "name": "App Store搶先預訂",
        "platform": "ios",
        "url": "https://apps.apple.com/tw/iphone/games",
    },
]

RPG_KEYWORDS = [
    "role playing",
    "角色扮演",
    "rpg",
    "mmorpg",
    "idle rpg",
    "action rpg",
    "card rpg",
    "roguelike",
    "fantasy",
    "英雄",
    "勇者",
    "冒險",
    "奇幻",
    "魔法",
]

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
        group = group.sort_values("rank").head(limit)
        parts.append(group)

    if not parts:
        return cleaned.drop(columns=["_row_order"]).iloc[0:0]

    cleaned = pd.concat(parts, ignore_index=True)
    cleaned = cleaned.drop(columns=["_row_order"])
    return cleaned


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
            keep="last"
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


def load_pre_registration_history():
    if not os.path.exists(PRE_REGISTRATION_HISTORY_FILE):
        return pd.DataFrame()
    return pd.read_csv(PRE_REGISTRATION_HISTORY_FILE)


def has_previous_pre_registration_history(history):
    if history.empty:
        return False
    return not history[history["date"] < TODAY].empty


def extract_google_play_app_id(href):
    match = re.search(r"[?&]id=([^&]+)", href)
    if not match:
        return ""
    return match.group(1).strip()


def to_absolute_google_play_url(href):
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://play.google.com" + href
    return "https://play.google.com/" + href


def extract_app_store_app_id(href):
    match = re.search(r"/id(\d+)", href)
    if not match:
        return ""
    return match.group(1).strip()


def to_absolute_app_store_url(href):
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://apps.apple.com" + href
    return "https://apps.apple.com/" + href


def contains_rpg_signal(*values):
    text = " ".join(str(value or "") for value in values).lower()
    return any(keyword.lower() in text for keyword in RPG_KEYWORDS)


def compact_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def find_json_value(data, target_keys):
    if isinstance(data, dict):
        for key, value in data.items():
            if key in target_keys and value:
                if isinstance(value, (dict, list)):
                    found = find_json_value(value, target_keys)
                    if found:
                        return found
                    continue
                return str(value)
            found = find_json_value(value, target_keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_json_value(item, target_keys)
            if found:
                return found
    return ""


def find_json_named_child(data, parent_keys):
    if isinstance(data, dict):
        for key, value in data.items():
            if key in parent_keys and value:
                name = find_json_value(value, {"name"})
                if name:
                    return name
            found = find_json_named_child(value, parent_keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_json_named_child(item, parent_keys)
            if found:
                return found
    return ""


def extract_app_store_release_date(page_text):
    patterns = [
        r"預計\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日)",
        r"預計\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月)",
        r"Expected\s+([A-Z][a-z]{2,9}\s+[0-9]{1,2},\s+[0-9]{4})",
        r"Expected\s+([A-Z][a-z]{2,9}\s+[0-9]{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            return compact_text(match.group(1))

    return ""


def fetch_google_play_app_detail(app_id, url):
    detail = {
        "app_name": "",
        "developer": "",
        "category": "",
        "is_rpg": False,
        "release_date": "",
    }

    try:
        detail_url = f"https://play.google.com/store/apps/details?id={app_id}&gl=TW&hl=zh_TW"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        resp = requests.get(detail_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        title = soup.find("h1")
        if title:
            detail["app_name"] = title.get_text(" ", strip=True)

        dev_link = soup.select_one("a[href*='/store/apps/dev']")
        if dev_link:
            detail["developer"] = dev_link.get_text(" ", strip=True)

        page_text = soup.get_text("\n", strip=True)
        for category_name in ["角色扮演", "Role Playing", "冒險", "Adventure", "策略", "Strategy"]:
            if category_name in page_text:
                detail["category"] = category_name
                break

        detail["is_rpg"] = contains_rpg_signal(
            detail["app_name"],
            detail["category"],
            page_text[:5000],
        )

    except Exception as e:
        print(f"[WARN] Google Play详情抓取失败 {app_id}: {e}")

    if not detail["app_name"]:
        detail["app_name"] = app_id

    detail["url"] = url
    return detail


def fetch_app_store_app_detail(app_id, url):
    detail = {
        "app_name": "",
        "developer": "",
        "category": "",
        "is_rpg": False,
        "release_date": "",
        "is_pre_order": False,
    }

    try:
        detail_url = url
        if "l=" not in detail_url:
            separator = "&" if "?" in detail_url else "?"
            detail_url = f"{detail_url}{separator}l=zh-Hant-TW"

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        resp = requests.get(detail_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        title = soup.find("h1")
        if title:
            detail["app_name"] = title.get_text(" ", strip=True)

        for script in soup.select("script[type='application/ld+json']"):
            raw_json = script.string or script.get_text()
            try:
                payload = json.loads(raw_json)
            except Exception:
                continue

            detail["app_name"] = detail["app_name"] or compact_text(find_json_value(payload, {"name"}))
            detail["developer"] = detail["developer"] or compact_text(
                find_json_named_child(payload, {"author", "publisher"})
            )
            detail["category"] = detail["category"] or compact_text(find_json_value(payload, {"applicationCategory", "genre"}))
            detail["release_date"] = detail["release_date"] or compact_text(find_json_value(
                payload,
                {"expectedReleaseDate", "releaseDate", "datePublished"},
            ))

        page_text = soup.get_text("\n", strip=True)

        if not detail["developer"]:
            developer_match = re.search(r"(?:開發者|Developer)\s*\n?\s*([^\n]+)", page_text)
            if developer_match:
                detail["developer"] = compact_text(developer_match.group(1))

        for category_name in ["角色扮演", "Role-Playing", "Role Playing", "冒險", "Adventure", "策略", "Strategy"]:
            if category_name in page_text:
                detail["category"] = category_name
                break

        detail["release_date"] = detail["release_date"] or extract_app_store_release_date(page_text)
        detail["is_pre_order"] = any(
            marker in page_text
            for marker in ["搶先預訂", "預訂", "Expected", "Pre-Order", "Pre-order"]
        )
        detail["is_rpg"] = contains_rpg_signal(
            detail["app_name"],
            detail["category"],
            page_text[:5000],
        )

    except Exception as e:
        print(f"[WARN] App Store详情抓取失败 {app_id}: {e}")

    if not detail["app_name"]:
        detail["app_name"] = app_id

    detail["url"] = url
    return detail


def fetch_google_play_pre_registration_games():
    rows = []
    seen = set()

    for source in PRE_REGISTRATION_SOURCES:
        if len(rows) >= PRE_REGISTRATION_DETAIL_LIMIT:
            break

        try:
            remaining_slots = PRE_REGISTRATION_DETAIL_LIMIT - len(rows)
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            resp = requests.get(source["url"], headers=headers, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            links = soup.select("a[href*='/store/apps/details?id=']")
            candidates = []

            for link in links:
                href = link.get("href", "")
                app_id = extract_google_play_app_id(href)

                if not app_id or app_id in seen:
                    continue

                app_name = link.get_text(" ", strip=True)
                url = to_absolute_google_play_url(href.split("&")[0])
                candidates.append({
                    "app_id": app_id,
                    "app_name": app_name,
                    "url": url,
                })
                seen.add(app_id)

                if len(candidates) >= remaining_slots:
                    break

            for candidate in candidates:
                detail = fetch_google_play_app_detail(candidate["app_id"], candidate["url"])
                app_name = detail.get("app_name") or candidate["app_name"] or candidate["app_id"]

                rows.append({
                    "date": TODAY,
                    "platform": source["platform"],
                    "region": "tw",
                    "region_name": REGIONS.get("tw", "台湾"),
                    "source_name": source["name"],
                    "source_url": source["url"],
                    "app_name": app_name,
                    "app_id": candidate["app_id"],
                    "developer": detail.get("developer", ""),
                    "category": detail.get("category", ""),
                    "is_rpg": bool(detail.get("is_rpg")),
                    "release_date": detail.get("release_date", ""),
                    "url": candidate["url"],
                })

            print(f"[OK] {source['name']}: {len(candidates)}")

        except Exception as e:
            print(f"[ERROR] {source['name']}: {e}")

    return rows


def fetch_app_store_pre_order_games():
    rows = []
    seen = set()

    for source in APP_STORE_PRE_ORDER_SOURCES:
        if len(rows) >= APP_STORE_PRE_ORDER_DETAIL_LIMIT:
            break

        try:
            remaining_slots = APP_STORE_PRE_ORDER_DETAIL_LIMIT - len(rows)
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            resp = requests.get(source["url"], headers=headers, timeout=30)
            resp.raise_for_status()

            html = resp.text
            section_start = html.find("搶先預訂")
            section_html = html

            if section_start >= 0:
                section_end = len(html)
                for marker in ["熱門 RPG", "熱門動作", "熱門休閒", "付費遊戲排行", "瀏覽類別"]:
                    marker_pos = html.find(marker, section_start + 1)
                    if marker_pos > 0:
                        section_end = min(section_end, marker_pos)
                section_html = html[section_start:section_end]

            soup = BeautifulSoup(section_html, "lxml")
            candidates = []

            for link in soup.select("a[href*='/tw/app/'][href*='/id']"):
                href = link.get("href", "")
                app_id = extract_app_store_app_id(href)

                if not app_id or app_id in seen:
                    continue

                app_name = link.get_text(" ", strip=True)
                url = to_absolute_app_store_url(href.split("?")[0])
                candidates.append({
                    "app_id": app_id,
                    "app_name": app_name,
                    "url": url,
                })
                seen.add(app_id)

                if len(candidates) >= remaining_slots:
                    break

            for candidate in candidates:
                detail = fetch_app_store_app_detail(candidate["app_id"], candidate["url"])

                if not detail.get("is_pre_order") and section_start < 0:
                    continue

                app_name = detail.get("app_name") or candidate["app_name"] or candidate["app_id"]
                rows.append({
                    "date": TODAY,
                    "platform": source["platform"],
                    "region": "tw",
                    "region_name": REGIONS.get("tw", "台湾"),
                    "source_name": source["name"],
                    "source_url": source["url"],
                    "app_name": app_name,
                    "app_id": candidate["app_id"],
                    "developer": detail.get("developer", ""),
                    "category": detail.get("category", ""),
                    "is_rpg": bool(detail.get("is_rpg")),
                    "release_date": detail.get("release_date", ""),
                    "url": candidate["url"],
                })

            print(f"[OK] {source['name']}: {len(rows)}")

        except Exception as e:
            print(f"[ERROR] {source['name']}: {e}")

    return rows


def fetch_pre_registration_games():
    rows = []
    rows.extend(fetch_google_play_pre_registration_games())
    rows.extend(fetch_app_store_pre_order_games())
    return rows


def save_pre_registration_rows(rows):
    if not rows:
        print("[WARN] 无预注册数据可保存")
        return

    df = pd.DataFrame(rows)

    if os.path.exists(PRE_REGISTRATION_HISTORY_FILE):
        old = pd.read_csv(PRE_REGISTRATION_HISTORY_FILE)
        first_seen_map = (
            old.sort_values("date")
            .drop_duplicates(subset=["platform", "region", "app_id"], keep="first")
            .set_index(["platform", "region", "app_id"])["first_seen_date"]
            .to_dict()
            if "first_seen_date" in old.columns else {}
        )
        df["first_seen_date"] = df.apply(
            lambda row: first_seen_map.get(
                (row["platform"], row["region"], row["app_id"]),
                TODAY,
            ),
            axis=1,
        )
        new_df = pd.concat([old, df], ignore_index=True)
        new_df = new_df.drop_duplicates(
            subset=["date", "platform", "region", "app_id"],
            keep="last",
        )
    else:
        df["first_seen_date"] = TODAY
        new_df = df

    new_df.to_csv(PRE_REGISTRATION_HISTORY_FILE, index=False, encoding="utf-8-sig")
    print(f"[OK] 预注册数据已保存：{PRE_REGISTRATION_HISTORY_FILE}")


def is_new_pre_registration(row, history):
    if history.empty:
        return True

    sub = history[
        (history["platform"] == row["platform"]) &
        (history["region"] == row["region"]) &
        (history["app_id"].astype(str) == str(row["app_id"])) &
        (history["date"] < TODAY)
    ]
    return sub.empty


def collect_new_pre_registrations(pre_registration_df, history):
    if pre_registration_df.empty:
        return pd.DataFrame()

    current = pre_registration_df.copy()
    current["is_new"] = current.apply(
        lambda row: is_new_pre_registration(row, history),
        axis=1,
    )
    return current[current["is_new"]]


def count_pre_registration_release_dates(pre_registration_df):
    if pre_registration_df.empty or "release_date" not in pre_registration_df.columns:
        return 0
    return int(pre_registration_df["release_date"].apply(compact_text).astype(bool).sum())


def format_pre_registration_platform(platform):
    if platform == "ios":
        return "iOS"
    if platform == "android":
        return "Google Play"
    return compact_text(platform) or "未知平台"


def format_pre_registration_item(row):
    new_text = "新发现｜" if bool(row.get("is_new")) else ""
    platform_text = format_pre_registration_platform(row.get("platform"))
    category_text = compact_text(row.get("category"))
    developer_text = compact_text(row.get("developer"))
    release_date = compact_text(row.get("release_date"))
    url = compact_text(row.get("url"))

    parts = [
        f"{new_text}{platform_text}",
        compact_text(row.get("app_name")),
    ]

    if category_text:
        parts.append(category_text)
    if developer_text:
        parts.append(developer_text)
    if release_date:
        parts.append(f"预计上线：{release_date}")
    if url:
        parts.append(url)

    return "｜".join(parts)


def has_previous_history(history):
    if history.empty:
        return False
    return not history[history["date"] < TODAY].empty


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


def is_key_chart(platform, chart_type):
    return (platform, chart_type) in KEY_CHARTS


def match_keyword_exact_or_contains(app_name, keyword):
    app_name = str(app_name).strip()
    keyword = str(keyword).strip()

    if not app_name or not keyword:
        return False

    if app_name == keyword:
        return True

    return keyword.lower() in app_name.lower()


def match_watch_app(df, watch):
    if df.empty:
        return pd.DataFrame()

    matched_parts = []

    apple_ids = [str(x) for x in watch.get("apple_ids", []) if str(x).strip()]
    google_packages = [str(x) for x in watch.get("google_packages", []) if str(x).strip()]
    keywords = [str(x) for x in watch.get("keywords", []) if str(x).strip()]

    if apple_ids:
        matched_parts.append(
            df[
                (df["platform"] == "ios") &
                (df["app_id"].astype(str).isin(apple_ids))
            ]
        )

    if google_packages:
        matched_parts.append(
            df[
                (df["platform"] == "android") &
                (df["app_id"].astype(str).isin(google_packages))
            ]
        )

    for keyword in keywords:
        mask = df["app_name"].apply(
            lambda x: match_keyword_exact_or_contains(x, keyword)
        )
        matched_parts.append(df[mask])

    if not matched_parts:
        return pd.DataFrame()

    matched = pd.concat(matched_parts, ignore_index=True)
    matched = matched.drop_duplicates(
        subset=["platform", "region", "chart_type", "app_id"]
    )

    return matched


def row_identity(row):
    return (
        row["platform"],
        row["region"],
        row["chart_type"],
        str(row["app_id"]),
    )


def build_watch_lookup(today_df):
    if today_df.empty:
        return {}

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
    if today_df.empty:
        return []

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
            row["app_id"]
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
            direction = "新进榜"
            text = (
                f"🆕 新进榜TOP{NEW_ENTRY_ALERT_RANK}｜{row['region_name']}｜"
                f"{chart_name}｜{rank}. {row['app_name']}"
            )
            magnitude = NEW_ENTRY_ALERT_RANK - rank + 1
        elif diff > 0:
            direction = "上涨"
            text = (
                f"🔥 大幅上涨｜{row['region_name']}｜{chart_name}｜"
                f"{row['app_name']}：{rank}（↑{diff}）"
            )
            magnitude = abs(diff)
        else:
            direction = "下跌"
            text = (
                f"⚠️ 大幅下跌｜{row['region_name']}｜{chart_name}｜"
                f"{row['app_name']}：{rank}（↓{abs(diff)}）"
            )
            magnitude = abs(diff)

        alerts.append({
            "text": text,
            "priority": priority,
            "watch_name": watch_name,
            "is_watch_app": is_watch_app,
            "is_key_chart": is_key_chart_value,
            "direction": direction,
            "magnitude": magnitude,
            "rank": rank,
            "app_name": row["app_name"],
            "chart_name": chart_name,
            "region_name": row["region_name"],
        })

    return sorted(
        alerts,
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2}.get(item["priority"], 3),
            0 if item["is_watch_app"] else 1,
            -item["magnitude"],
            item["rank"],
        )
    )


def build_business_summary(lines, today_df, history, pre_registration_df, pre_registration_history):
    lines.append("========== 今日业务摘要 ==========")

    if today_df.empty and pre_registration_df.empty:
        lines.append("今日未抓取到榜单和预注册数据，暂无法判断业务异动。")
        lines.append("")
        return

    alerts = collect_alerts(today_df, history)
    p0_alerts = [item for item in alerts if item["priority"] == "P0"]
    p1_alerts = [item for item in alerts if item["priority"] == "P1"]
    new_pre_registrations = collect_new_pre_registrations(
        pre_registration_df,
        pre_registration_history,
    )
    new_rpg_pre_registrations = (
        new_pre_registrations[new_pre_registrations["is_rpg"]]
        if not new_pre_registrations.empty else pd.DataFrame()
    )

    if not has_previous_history(history):
        lines.append("今日主要用于建立历史基准，明日起可输出涨跌和连续趋势判断。")
    elif p0_alerts:
        lines.append(f"重点风险/机会：发现 {len(p0_alerts)} 条 P0 级重点异动，需要优先关注。")
    elif p1_alerts:
        lines.append(f"重点风险/机会：发现 {len(p1_alerts)} 条 P1 级异动，建议关注是否由活动或投放导致。")
    else:
        lines.append("重点风险/机会：暂无高优先级异动，整体波动处于常规范围。")

    if pre_registration_df.empty:
        lines.append("预注册：今日未抓取到预注册游戏数据。")
    elif not has_previous_pre_registration_history(pre_registration_history):
        rpg_count = int(pre_registration_df["is_rpg"].sum())
        ios_count = int((pre_registration_df["platform"] == "ios").sum())
        android_count = int((pre_registration_df["platform"] == "android").sum())
        release_date_count = count_pre_registration_release_dates(pre_registration_df)
        lines.append(
            f"预注册：今日建立基准，当前发现 {len(pre_registration_df)} 款预注册游戏"
            f"（iOS {ios_count} / Google Play {android_count}），角色扮演相关 {rpg_count} 款，"
            f"已抓取预计上线日期 {release_date_count} 款。"
        )
    elif not new_pre_registrations.empty:
        new_ios_count = int((new_pre_registrations["platform"] == "ios").sum())
        new_android_count = int((new_pre_registrations["platform"] == "android").sum())
        new_release_date_count = count_pre_registration_release_dates(new_pre_registrations)
        lines.append(
            f"预注册：今日新发现 {len(new_pre_registrations)} 款预注册游戏"
            f"（iOS {new_ios_count} / Google Play {new_android_count}），"
            f"角色扮演相关 {len(new_rpg_pre_registrations)} 款，已抓取预计上线日期 {new_release_date_count} 款。"
        )
    else:
        lines.append("预注册：今日暂无新发现的预注册游戏。")

    watch_highlights = []
    if not today_df.empty:
        for watch in WATCH_APPS:
            matched = match_watch_app(today_df, watch)
            if matched.empty:
                continue

            matched = matched.copy()
            matched["is_key_chart"] = matched.apply(
                lambda row: is_key_chart(row["platform"], row["chart_type"]),
                axis=1
            )
            matched = matched.sort_values(
                ["is_key_chart", "platform", "rank"],
                ascending=[False, True, True]
            )

            row = matched.iloc[0]
            previous_rank = get_previous_rank(
                history,
                row["platform"],
                row["region"],
                row["chart_type"],
                row["app_id"]
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
            trend_note = format_trend_note(history, row)
            trend_text = f"；{trend_note}" if trend_note else ""

            lines.append(
                f"{row['region_name']}｜{platform_name}｜{chart_name}："
                f"{int(row['rank'])}（{change}{trend_text}）"
            )

    if not has_watch_result:
        lines.append("今日重点产品未进入已抓取榜单范围。")


def build_pre_registration_section(lines, pre_registration_df, pre_registration_history):
    lines.append("")
    lines.append("========== 新游预注册监控 ==========")

    if pre_registration_df.empty:
        lines.append("今日未抓取到预注册游戏数据。")
        lines.append("")
        return

    current = pre_registration_df.copy()
    current["is_new"] = current.apply(
        lambda row: is_new_pre_registration(row, pre_registration_history),
        axis=1,
    )
    current = current.sort_values(["is_new", "is_rpg", "app_name"], ascending=[False, False, True])

    new_items = current[current["is_new"]]
    rpg_items = current[current["is_rpg"]]
    new_rpg_items = new_items[new_items["is_rpg"]] if not new_items.empty else pd.DataFrame()
    ios_count = int((current["platform"] == "ios").sum())
    android_count = int((current["platform"] == "android").sum())
    release_date_count = count_pre_registration_release_dates(current)

    if not has_previous_pre_registration_history(pre_registration_history):
        lines.append(
            f"今日建立预注册基准：共发现 {len(current)} 款预注册游戏"
            f"（iOS {ios_count} / Google Play {android_count}），其中角色扮演相关 {len(rpg_items)} 款，"
            f"已抓取预计上线日期 {release_date_count} 款。"
        )
    elif new_items.empty:
        lines.append(f"今日暂无新发现的预注册游戏；当前仍在监控 {len(current)} 款。")
    else:
        new_ios_count = int((new_items["platform"] == "ios").sum())
        new_android_count = int((new_items["platform"] == "android").sum())
        new_release_date_count = count_pre_registration_release_dates(new_items)
        lines.append(
            f"今日新发现 {len(new_items)} 款预注册游戏"
            f"（iOS {new_ios_count} / Google Play {new_android_count}），"
            f"其中角色扮演相关 {len(new_rpg_items)} 款，已抓取预计上线日期 {new_release_date_count} 款。"
        )

    priority_items = pd.concat([new_rpg_items, rpg_items], ignore_index=True)
    if not priority_items.empty:
        priority_items = priority_items.drop_duplicates(subset=["platform", "region", "app_id"])
        lines.append("")
        lines.append("【角色扮演优先关注】")

        for _, row in priority_items.head(PRE_REGISTRATION_DISPLAY_LIMIT).iterrows():
            lines.append(format_pre_registration_item(row))
    else:
        lines.append("当前未识别到角色扮演相关预注册游戏。")

    other_new_items = new_items[~new_items["is_rpg"]] if not new_items.empty else pd.DataFrame()
    if not other_new_items.empty:
        lines.append("")
        lines.append("【其他新发现】")

        for _, row in other_new_items.head(10).iterrows():
            lines.append(format_pre_registration_item(row))

    lines.append("")


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
        if item["github_url"]:
            lines.append(
                f"{idx}. {item['watch_name']}｜{item['region_name']}｜{item['chart_name']}：{item['github_url']}"
            )
        else:
            lines.append(
                f"{idx}. {item['watch_name']}｜{item['region_name']}｜{item['chart_name']}：{item['file_path']}"
            )


def send_feishu_text(text):
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
        print("[OK] 飞书文本推送成功")
    except Exception as e:
        print(f"[ERROR] 飞书文本推送失败: {e}")
        print(text)


def build_report(today_rows, pre_registration_rows):
    history = load_history()
    pre_registration_history = load_pre_registration_history()
    today_df = pd.DataFrame(today_rows)
    pre_registration_df = pd.DataFrame(pre_registration_rows)

    lines = []
    lines.append("【台湾手游榜单监控日报 V2.7】")
    lines.append(f"日期：{TODAY}")
    lines.append("")

    if today_df.empty and pre_registration_df.empty:
        lines.append("今日未抓取到榜单和预注册数据，请检查 GitHub Actions 日志。")
        return "\n".join(lines)

    build_business_summary(lines, today_df, history, pre_registration_df, pre_registration_history)

    if not today_df.empty:
        build_watch_section(lines, today_df, history)

    build_pre_registration_section(lines, pre_registration_df, pre_registration_history)

    if not today_df.empty:
        build_alert_section(lines, today_df, history)
        build_top_section(lines, today_df, history)

    chart_infos = generate_trend_charts(history)
    build_trend_section(lines, chart_infos)

    return "\n".join(lines)


def main():
    all_rows = []
    pre_registration_rows = fetch_pre_registration_games()

    for region in REGIONS.keys():
        for chart_type in IOS_CHARTS.keys():
            all_rows.extend(fetch_ios_chart(region, chart_type))

        for chart_type in ANDROID_CHARTS.keys():
            all_rows.extend(fetch_android_chart(region, chart_type))

    all_rows = clean_rank_rows(pd.DataFrame(all_rows)).to_dict("records")

    print(f"TOTAL ROWS: {len(all_rows)}")

    save_rows(all_rows)
    save_pre_registration_rows(pre_registration_rows)

    report = build_report(all_rows, pre_registration_rows)

    report_path = os.path.join(DATA_DIR, f"daily_report_{TODAY}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    send_feishu_text(report)


if __name__ == "__main__":
    main()
