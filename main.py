# -*- coding: utf-8 -*-
"""
ボートレース予想・検証ツール v25.6 Explainableログ版
- Pyto/iPhone想定
- 取得が止まる場所を特定するため、GET開始/完了/BS開始/完了を表示
- 全requestsにtimeoutを設定
- 直近Nレース探索はrace-detail存在優先で緩く追加
- CSV/JSON保存先は書き込み可能な場所を自動選択
- v21: AI3連対率/AI予測1着率/展示タイム順位/展示ST順位/進入安定度/決まり手率を予想スコアへ反映

まずは：モード5/6 → 件数20 → 詳細y でテスト推奨。1レースごとにCSV追記保存・再開対応。
"""

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import json
import requests
from datetime import datetime, timedelta
import os
import csv
import hashlib
import time
import unicodedata
import argparse
import sys
import glob

try:
    import pyperclip
except Exception:
    pyperclip = None

BASE_URL = "https://boaters-boatrace.com"
LIST_URL = BASE_URL + "/race"
REQUEST_TIMEOUT = 8
SLEEP_SEC = 0.15

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# v20: 情報量は減らさず、接続だけ使い回して高速化する。
SESSION = requests.Session()
SESSION.headers.update(headers)

PLACE_MAP = {
    "桐生": "kiryu", "戸田": "toda", "江戸川": "edogawa", "平和島": "heiwajima",
    "多摩川": "tamagawa", "浜名湖": "hamanako", "蒲郡": "gamagori", "常滑": "tokoname",
    "津": "tsu", "三国": "mikuni", "びわこ": "biwako", "住之江": "suminoe",
    "尼崎": "amagasaki", "鳴門": "naruto", "丸亀": "marugame", "児島": "kojima",
    "宮島": "miyajima", "徳山": "tokuyama", "下関": "shimonoseki", "若松": "wakamatsu",
    "芦屋": "ashiya", "福岡": "fukuoka", "唐津": "karatsu", "からつ": "karatsu",
    "大村": "omura"
}

PLACE_MAP_REV = {v: k for k, v in PLACE_MAP.items()}
PLACE_MAP_REV["karatsu"] = "唐津"

# v25.3: 場の水質カテゴリ。条件抽出・場別補正の土台。
# ※分類は運用上の仮分類。必要に応じて後で場ごとに補正する。
WATER_TYPE_MAP = {
    "kiryu": "淡水",
    "toda": "淡水",
    "edogawa": "汽水",
    "heiwajima": "海水",
    "tamagawa": "淡水",
    "hamanako": "汽水",
    "gamagori": "海水",
    "tokoname": "海水",
    "tsu": "海水",
    "mikuni": "淡水",
    "biwako": "淡水",
    "suminoe": "淡水",
    "amagasaki": "淡水",
    "naruto": "海水",
    "marugame": "海水",
    "kojima": "海水",
    "miyajima": "海水",
    "tokuyama": "海水",
    "shimonoseki": "海水",
    "wakamatsu": "海水",
    "ashiya": "淡水",
    "fukuoka": "海水",
    "karatsu": "淡水",
    "omura": "海水",
}

JCD_MAP = {
    "kiryu": "01", "toda": "02", "edogawa": "03", "heiwajima": "04",
    "tamagawa": "05", "hamanako": "06", "gamagori": "07", "tokoname": "08",
    "tsu": "09", "mikuni": "10", "biwako": "11", "suminoe": "12",
    "amagasaki": "13", "naruto": "14", "marugame": "15", "kojima": "16",
    "miyajima": "17", "tokuyama": "18", "shimonoseki": "19", "wakamatsu": "20",
    "ashiya": "21", "fukuoka": "22", "karatsu": "23", "omura": "24"
}


def get_save_dir():
    candidates = [
        os.getcwd(),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~"),
        "/tmp",
    ]
    for d in candidates:
        try:
            if not d:
                continue
            os.makedirs(d, exist_ok=True)
            test_path = os.path.join(d, "_write_test.tmp")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_path)
            return d
        except Exception:
            continue
    return "."

SAVE_DIR = get_save_dir()


def save_path(filename):
    return os.path.join(SAVE_DIR, filename)


def html_sig(text):
    try:
        return hashlib.md5(text[:5000].encode("utf-8", errors="ignore")).hexdigest()[:10]
    except Exception:
        return ""


def get_html(url, label="", debug=False):
    if debug:
        print("[GET開始]", label, url)
    try:
        # v19: requests.Sessionで接続を使い回す。取得情報量は減らさない。
        r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        status = r.status_code
        text = r.text or ""
        if debug:
            print("[GET完了]", "status=", status, "len=", len(text), "sig=", html_sig(text))
        r.raise_for_status()
        time.sleep(SLEEP_SEC)
        return text
    except Exception as e:
        if debug:
            print("[GET失敗]", type(e).__name__, str(e))
        raise


def make_soup(html, debug=False):
    if debug:
        print("[BS開始] len=", len(html))
    soup = BeautifulSoup(html, "lxml")
    if debug:
        print("[BS完了]")
    return soup


def is_top_like_html(html):
    """
    v24: URL監査用の安全判定。
    以前は未定義でモード7が落ちていたため追加。
    厳しく弾きすぎると /data 等の有効ページを捨てるので、
    明らかな404/トップ遷移だけを True にする。
    """
    if not html:
        return True
    head = html[:12000]
    ng_words = [
        "404", "Not Found", "ページが見つかりません", "存在しません",
        "指定されたページ", "エラーが発生", "該当するレース"
    ]
    if any(w in head for w in ng_words):
        return True
    return False


def race_base_url(url):
    """
    https://.../race/{place}/{date}/{race_no}/{page} から
    https://.../race/{place}/{date}/{race_no} を返す。
    """
    m = re.search(r"(https?://[^/]+/race/[^/]+/\d{4}-\d{2}-\d{2}/\d+R)(?:/[^/?#]+)?", url)
    if m:
        return m.group(1)
    m = re.search(r"(/race/[^/]+/\d{4}-\d{2}-\d{2}/\d+R)(?:/[^/?#]+)?", url)
    if m:
        return urljoin(BASE_URL, m.group(1))
    return url.rsplit("/", 1)[0]


def build_race_page_urls(url):
    """
    v24: BOATERSの画面役割ごとにURLを明示生成。
    race-detail: 出走表
    data: 連対率・展開・AI3連対率・AIオッズ評価
    odds: オッズ
    last-minute: 直前情報・展示・気象
    waku: 枠別実績
    motor: モーター情報
    result/race-result: 結果候補
    """
    base = race_base_url(url)
    pages = [
        "race-detail",
        "data",
        "odds",
        "last-minute",
        "waku",
        "motor",
        "race-result",
        "result",
    ]
    return [(p, base + "/" + p) for p in pages]


def safe_float(v):
    try:
        s = str(v).replace("%", "").replace("F", "").replace("L", "").strip()
        if s in ["", "-", "None"]:
            return None
        return float(s)
    except Exception:
        return None


def safe_int(v):
    try:
        s = str(v).replace(",", "").replace("円", "").strip()
        return int(float(s))
    except Exception:
        return 0


def get_race_parts(url):
    parts = url.split("/")
    try:
        place = parts[4]
        race_date = parts[5]
        race_no = parts[6]
        return place, race_date, race_no
    except Exception:
        return "", "", ""


def race_no_to_int(race_no):
    m = re.search(r"(\d+)", str(race_no))
    return int(m.group(1)) if m else 0


def get_race_title(url):
    place, race_date, race_no = get_race_parts(url)
    return f"{PLACE_MAP_REV.get(place, place)} {race_date} {race_no}"


def extract_deadline_from_text(text):
    patterns = [
        r"締切予定時刻\s*[:：]?\s*(\d{1,2}:\d{2})",
        r"締切予定\s*[:：]?\s*(\d{1,2}:\d{2})",
        r"締切時刻\s*[:：]?\s*(\d{1,2}:\d{2})",
        r"締切\s*[:：]?\s*(\d{1,2}:\d{2})",
        r"投票締切\s*[:：]?\s*(\d{1,2}:\d{2})",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    times = re.findall(r"\b\d{1,2}:\d{2}\b", text)
    for t in times:
        h, _ = map(int, t.split(":"))
        if 8 <= h <= 21:
            return t
    return ""


def get_race_urls(debug=False):
    html = get_html(LIST_URL, "race list", debug=debug)
    matches = re.findall(r'/race/[^"\'<>\s]+/race-detail', html)
    urls = []
    for m in matches:
        full_url = urljoin(BASE_URL, m)
        if full_url not in urls:
            urls.append(full_url)
    return urls


def is_before_deadline(race_date, deadline):
    if not deadline:
        return True
    try:
        now = datetime.now()
        h, m = map(int, deadline.split(":"))
        y, mo, d = map(int, race_date.split("-"))
        return now < datetime(y, mo, d, h, m)
    except Exception:
        return True


def get_available_races():
    print("レース情報を確認中です。少し待ってください...")
    races = get_race_urls(debug=True)
    available = []
    for url in races:
        try:
            html = get_html(url, get_race_title(url), debug=False)
            soup = make_soup(html)
            text = soup.get_text("\n", strip=True)
            deadline = extract_deadline_from_text(text)
            place, race_date, race_no = get_race_parts(url)
            if is_before_deadline(race_date, deadline):
                available.append({
                    "url": url,
                    "place": place,
                    "place_jp": PLACE_MAP_REV.get(place, place),
                    "date": race_date,
                    "race_no": race_no,
                    "race_num": race_no_to_int(race_no),
                    "deadline": deadline,
                })
        except Exception as e:
            print("一覧詳細スキップ:", url, e)
    available.sort(key=lambda x: (x["date"], x["place"], x["race_num"]))
    return available


def normalize_query(q):
    q = q.strip().replace("Ｒ", "R").replace("レース", "R").replace(" ", "").replace("　", "")
    q = q.replace("唐唐津", "唐津").replace("津津", "津")
    m = re.search(r"(.+?)(\d{1,2})R?$", q)
    if not m:
        return None, None
    return PLACE_MAP.get(m.group(1)), m.group(2) + "R"


def select_race_by_query(available_races, query):
    place_code, race_no = normalize_query(query)
    if not place_code:
        return None
    for r in available_races:
        if r["place"] == place_code and r["race_no"] == race_no:
            return r["url"]
    return None


def parse_racers_from_text(text):
    # かなり緩い抽出。サイトHTML構造変化でも最低限の数値を拾う目的。
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    names = []
    # 「枠写真レーサー」周辺ではなく、レーサー名リンクから取れない場合に備え簡易候補。
    return lines, names



def get_race_no_category(race_no):
    n = race_no_to_int(race_no)
    if n <= 0:
        return "不明"
    if n <= 3:
        return "朝・序盤"
    if n <= 8:
        return "中盤"
    if n <= 10:
        return "終盤前"
    return "終盤・勝負所"


def extract_series_day(text):
    """節間日を緩く抽出。取れない場合は空欄。"""
    if not text:
        return ""
    # 強い意味のある語を優先
    for key in ["優勝戦", "準優勝戦", "準優", "最終日", "初日"]:
        if key in text:
            return key
    m = re.search(r"([2-6２-６])\s*日目", text)
    if m:
        d = unicodedata.normalize("NFKC", m.group(1))
        return d + "日目"
    return ""


def estimate_psychology_context(race_no, series_day):
    """心理仮説を数値化するためのタグ。断定ではなく後で検証する素材。"""
    n = race_no_to_int(race_no)
    tags = []
    score = 0
    if n <= 3 and n > 0:
        tags.append("序盤様子見仮説")
        score -= 2
    elif 4 <= n <= 8:
        tags.append("中盤着取り仮説")
        score += 0
    elif n >= 9:
        tags.append("終盤勝負駆け仮説")
        score += 3
    if series_day in ["最終日", "準優", "準優勝戦", "優勝戦"]:
        tags.append("節間勝負度高め")
        score += 4
    elif series_day == "初日":
        tags.append("初日様子見仮説")
        score -= 1
    elif series_day in ["3日目", "4日目", "5日目"]:
        tags.append("予選勝負駆け仮説")
        score += 2
    return {"心理タグ": tags, "心理スコア": score}


def build_player_profile_material(racers):
    """将来の選手DB用の素材。今は予想に強く使いすぎず、CSVへ残す。"""
    out = []
    for r in racers:
        out.append({
            "枠": r.get("枠", ""),
            "選手名": r.get("選手名", ""),
            "級別": r.get("級別", ""),
            "平均ST": r.get("平均ST", ""),
            "F持ち": r.get("F持ち", 0),
            "全国勝率": r.get("全国勝率", ""),
            "当地勝率": r.get("当地勝率", ""),
            "モーター2連率": r.get("モーター2連率", ""),
            "モーター3連率": r.get("モーター3連率", ""),
        })
    return out


# ===== v21: 詳細・AI・展示・展開情報の活用 =====

def pct_to_float(v):
    return safe_float(v)


def normalize_pct_token(x):
    if x is None:
        return ""
    s = unicodedata.normalize("NFKC", str(x)).replace("％", "%").strip()
    return s


def is_percent_token(x):
    s = normalize_pct_token(x)
    return bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?%?", s))


def token_float(x):
    s = normalize_pct_token(x).replace("%", "")
    return safe_float(s)


def find_section_lines(lines, start_keywords, end_keywords=None, max_len=300):
    start = -1
    for i, line in enumerate(lines):
        if all(k in line for k in start_keywords):
            start = i
            break
    if start < 0:
        return []
    end = min(len(lines), start + max_len)
    if end_keywords:
        for j in range(start + 1, min(len(lines), start + max_len)):
            if any(k in lines[j] for k in end_keywords):
                end = j
                break
    return lines[start:end]


def find_section_any(lines, start_keywords, end_keywords=None, max_len=300):
    start = -1
    for i, line in enumerate(lines):
        if any(k in line for k in start_keywords):
            start = i
            break
    if start < 0:
        return []
    end = min(len(lines), start + max_len)
    if end_keywords:
        for j in range(start + 1, min(len(lines), start + max_len)):
            if any(k in lines[j] for k in end_keywords):
                end = j
                break
    return lines[start:end]


def norm_name(s):
    return re.sub(r"\s+", "", str(s or ""))


def collect_after_name(section, name, max_items=35):
    if not section or not name:
        return []
    nn = norm_name(name)
    for i, x in enumerate(section):
        nx = norm_name(x)
        if nx == nn or (nn and nn in nx):
            return section[i:i + max_items]
    return []


def assign_ai_metrics(racers, lines):
    section = find_section_any(lines, ["AI3連対率", "3連対率1着率", "AI3連対率3連対率"], ["AI3連対率の着順別表示", "先頭艇別"], 650)
    if not section:
        return
    for r in racers:
        blk = collect_after_name(section, r.get("選手名", ""), 35)
        pcts = []
        for x in blk:
            if "%" in x or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", normalize_pct_token(x)):
                fv = token_float(x)
                if fv is not None and 0 <= fv <= 100:
                    pcts.append(fv)
        if len(pcts) >= 1:
            r["AI3連対率"] = pcts[0]
        if len(pcts) >= 2:
            r["実績3連対率"] = pcts[1]
        if len(pcts) >= 3:
            r["AI1着率"] = pcts[2]
        if len(pcts) >= 4:
            r["AI2着率"] = pcts[3]
        if len(pcts) >= 5:
            r["AI3着率"] = pcts[4]


def assign_ai_odds_value(racers, lines):
    section = find_section_any(lines, ["AIオッズ評価", "オッズの妙味度", "1着投票率", "AI予測"], ["前づけデータ", "進入コース変更"], 650)
    if not section:
        return
    for r in racers:
        blk = collect_after_name(section, r.get("選手名", ""), 35)
        if not blk:
            continue
        joined = "\n".join(blk)
        for word in ["かなり過小", "やや過小", "過小", "妥当", "やや過大", "過大"]:
            if word in joined:
                r["オッズ妙味"] = word
                break
        pcts = []
        for x in blk:
            if "%" in x or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", normalize_pct_token(x)):
                fv = token_float(x)
                if fv is not None and 0 <= fv <= 100:
                    pcts.append(fv)
        if len(pcts) >= 2:
            r["1着投票率"] = pcts[-2]
            r["AI予測1着率"] = pcts[-1]
            r["AI妙味差"] = round(r["AI予測1着率"] - r["1着投票率"], 1)


def assign_exhibition_times(racers, lines):
    # 現在展示は「調整重量/体重/展示タイム/チルト」から「スタート情報」までを優先。
    section = find_section_any(lines, ["枠レーサー調整重量", "調整重量", "買い目メモ"], ["スタート情報", "全国枠番実績", "発走直前"], 280)
    # モーター表の「展示タイム」だけを拾わないよう、選手名が6名程度入る区間を優先する。
    if section:
        name_hits = sum(1 for r in racers if any(r.get("選手名", "") == x for x in section))
        if name_hits < 3:
            section = []
    if not section:
        # 買い目メモ後にあるケース
        start = -1
        for i, x in enumerate(lines):
            if "買い目メモ" in x:
                start = i
                break
        if start >= 0:
            section = lines[start:start+220]
    for r in racers:
        blk = collect_after_name(section, r.get("選手名", ""), 20)
        if not blk:
            continue
        ex_time = None
        tilt = ""
        for x in blk:
            sx = normalize_pct_token(x)
            # 展示タイムは概ね6.50〜7.40
            m = re.fullmatch(r"([67]\.[0-9]{2})", sx)
            if m and ex_time is None:
                ex_time = float(m.group(1))
                continue
            if sx in ["-0.5", "0.0", "0.5", "1.0", "-1.0"] and tilt == "":
                tilt = sx
        if ex_time is not None:
            r["展示タイム"] = ex_time
        if tilt:
            r["チルト"] = tilt
    times = [(r.get("枠"), r.get("展示タイム")) for r in racers if isinstance(r.get("展示タイム"), (int, float))]
    if times:
        min_t = min(t for _, t in times)
        max_t = max(t for _, t in times)
        sorted_times = sorted(times, key=lambda x: x[1])
        rank_by_frame = {}
        last_t = None
        last_rank = 0
        for idx, (frame, t) in enumerate(sorted_times, start=1):
            if last_t is None or abs(t - last_t) > 1e-9:
                last_rank = idx
                last_t = t
            rank_by_frame[frame] = last_rank
        for r in racers:
            t = r.get("展示タイム")
            if isinstance(t, (int, float)):
                r["展示順位"] = rank_by_frame.get(r.get("枠"), "")
                r["展示差"] = round(t - min_t, 2)
        return {"展示最速": min_t, "展示最遅": max_t, "展示レンジ": round(max_t - min_t, 2)}
    return {"展示最速": "", "展示最遅": "", "展示レンジ": ""}


def assign_start_exhibition_st(racers, lines):
    section = find_section_any(lines, ["スタート情報", "枠並びST"], ["水面気象情報", "オッズ", "全国枠番実績"], 180)
    if not section:
        return
    sts = []
    for x in section:
        sx = normalize_pct_token(x)
        m = re.search(r"(F)?\.?([0-9]{2,3})", sx)
        # .112 / F.01 / 0.112 のようなものだけ拾う。気温などを避けるため、水面気象前区間限定。
        if sx.startswith(".") or sx.startswith("F.") or re.fullmatch(r"0\.[0-9]{2,3}", sx):
            f = sx.startswith("F")
            val = None
            try:
                val = float(sx.replace("F", "").replace(".", "0.", 1) if sx.startswith(".") else sx.replace("F", ""))
            except Exception:
                try:
                    val = float("0." + re.sub(r"\D", "", sx))
                except Exception:
                    val = None
            if val is not None:
                sts.append((val, f, sx))
        elif re.fullmatch(r"F\.?[0-9]{2}", sx):
            try:
                val = float("0." + re.sub(r"\D", "", sx))
                sts.append((val, True, sx))
            except Exception:
                pass
    if len(sts) >= 6:
        sts = sts[:6]
        for r, (val, f, raw) in zip(racers, sts):
            r["展示ST"] = raw
            r["展示ST数値"] = val
            r["展示F"] = 1 if f else 0
        sorted_st = sorted([(r.get("枠"), r.get("展示ST数値", 9)) for r in racers], key=lambda x: x[1])
        for idx, (frame, _) in enumerate(sorted_st, start=1):
            for r in racers:
                if r.get("枠") == frame:
                    r["展示ST順位"] = idx


def assign_entry_stability(racers, lines):
    section = find_section_any(lines, ["前づけデータ", "1コース2コース3コース"], ["進入コース変更", "全国枠番実績", "買い目メモ"], 650)
    if not section:
        return
    for r in racers:
        blk = collect_after_name(section, r.get("選手名", ""), 45)
        pcts = []
        for x in blk:
            if "%" in x:
                fv = token_float(x)
                if fv is not None:
                    pcts.append(fv)
        if pcts:
            r["進入安定度"] = max(pcts)


def assign_kimarite_rates(racers, lines):
    section = find_section_any(lines, ["決まり手率", "逃げ差され", "逃し差しまくり"], ["AIオッズ", "前づけデータ"], 650)
    if not section:
        return
    for r in racers:
        blk = collect_after_name(section, r.get("選手名", ""), 35)
        pcts = []
        for x in blk:
            if "%" in x:
                fv = token_float(x)
                if fv is not None:
                    pcts.append(fv)
        if not pcts:
            continue
        if r.get("枠") == 1:
            r["逃げ率"] = pcts[0]
            if len(pcts) > 1: r["差され率"] = pcts[1]
            if len(pcts) > 2: r["まくられ率"] = pcts[2]
            if len(pcts) > 3: r["まくられ差し率"] = pcts[3]
        else:
            # 外枠は 差し/まくり/まくり差し の順であることが多い。
            r["差し率"] = pcts[0]
            if len(pcts) > 1: r["まくり率"] = pcts[1]
            if len(pcts) > 2: r["まくり差し率"] = pcts[2]


def enrich_race_features(race_data, text, lines, role_lines=None):
    """
    v24.1: 取得元URLを分けて特徴を入れる。
      /data        -> AI3連対率・AIオッズ評価・妙味・進入安定・決まり手率
      /last-minute -> 当日展示タイム・展示ST・気象
      /motor       -> モーター展示平均/順位の補助
    """
    racers = race_data.get("出走表", [])
    if not racers:
        race_data["展示サマリ"] = {"展示最速": "", "展示最遅": "", "展示レンジ": ""}
        return race_data

    role_lines = role_lines or {}
    data_lines = role_lines.get("data") or lines
    last_lines = role_lines.get("last-minute") or lines
    motor_lines = role_lines.get("motor") or lines
    combined_lines = lines

    # /data 系
    assign_ai_metrics(racers, data_lines)
    assign_ai_odds_value(racers, data_lines)
    assign_entry_stability(racers, data_lines)
    assign_kimarite_rates(racers, data_lines)

    # /last-minute 系: 当日展示・展示ST
    ex_summary = assign_exhibition_times(racers, last_lines)
    if not ex_summary.get("展示レンジ"):
        # 補助: motorページに現在選手が含まれる場合のみ拾える
        ex_summary2 = assign_exhibition_times(racers, motor_lines)
        if ex_summary2.get("展示レンジ"):
            ex_summary = ex_summary2
    assign_start_exhibition_st(racers, last_lines)

    # 気象は last-minute 優先
    last_text = "\n".join(last_lines)
    w = extract_weather_from_text(last_text)
    if any(w.values()):
        race_data["水面気象情報"] = w

    race_data["展示サマリ"] = ex_summary
    race_data["選手プロファイル素材"] = build_player_profile_material(racers)

    # v24.1 監査: どのURLから特徴が取れているか
    r1 = racers[0] if racers else {}
    race_data["v241取得元監査"] = {
        "data_AI3_keyword": "AI3連対率" in "\n".join(data_lines),
        "data_odds_keyword": ("AIオッズ評価" in "\n".join(data_lines) or "オッズの妙味度" in "\n".join(data_lines)),
        "last_minute_st_keyword": "スタート情報" in "\n".join(last_lines),
        "last_minute_weather_keyword": "水面気象情報" in "\n".join(last_lines),
        "motor_exhibition_keyword": "展示順位" in "\n".join(motor_lines),
        "r1_AI3": r1.get("AI3連対率", ""),
        "r1_AI1": r1.get("AI予測1着率", r1.get("AI1着率", "")),
        "r1_ex_rank": r1.get("展示順位", ""),
        "r1_ex_st_rank": r1.get("展示ST順位", ""),
    }
    return race_data


def get_frame(race_data, frame):
    for r in race_data.get("出走表", []):
        if safe_int(r.get("枠")) == frame:
            return r
    return {}


def classify_race_style(race_data, inner_score, stability_score):
    racers = race_data.get("出走表", [])
    r1 = get_frame(race_data, 1)
    r3 = get_frame(race_data, 3)
    r4 = get_frame(race_data, 4)
    weather = race_data.get("水面気象情報", {})
    wind = safe_float(weather.get("風速")) or 0
    wave = safe_float(weather.get("波高")) or 0
    hard = 0
    rough = 0
    reasons = []

    hard += max(0, inner_score - 50) * 0.8
    hard += max(0, stability_score - 70) * 0.4
    if (safe_float(r1.get("AI予測1着率")) or 0) >= 65:
        hard += 15; reasons.append("1号艇AI1着強")
    if (safe_float(r1.get("逃げ率")) or 0) >= 60:
        hard += 10; reasons.append("1号艇逃げ率高")
    if safe_int(r1.get("展示順位")) in [1, 2]:
        hard += 6; reasons.append("1号艇展示上位")
    if wind <= 3 and wave <= 3:
        hard += 5; reasons.append("水面安定")

    if inner_score < 55:
        rough += 18; reasons.append("1号艇信頼低")
    if wind >= 4 or wave >= 3:
        rough += 10; reasons.append("水面荒れ補正")
    if (safe_float(r1.get("展示差")) or 0) >= 0.05:
        rough += 8; reasons.append("1号艇展示劣勢")
    if safe_int(r1.get("展示ST順位")) >= 4:
        rough += 5; reasons.append("1号艇展示ST劣勢")
    attack = max(
        safe_float(r3.get("まくり率")) or 0,
        safe_float(r3.get("まくり差し率")) or 0,
        safe_float(r4.get("まくり率")) or 0,
        safe_float(r4.get("まくり差し率")) or 0,
    )
    if attack >= 8:
        rough += 10; reasons.append("3/4攻め率あり")

    if hard >= 45 and rough < 20:
        style = "硬め本線"
    elif rough >= 28:
        style = "穴狙い"
    else:
        style = "中間"
    return {"レース分類": style, "硬さ指数": round(hard, 1), "荒れ指数": round(rough, 1), "分類理由": reasons}


def split_trifecta(value):
    if not value or not is_valid_trifecta(value):
        return "", "", ""
    a, b, c = value.split("-")
    return a, b, c


def inner_confidence_bucket(v):
    n = safe_int(v)
    if n >= 70:
        return "70以上"
    if n >= 60:
        return "60-69"
    if n >= 50:
        return "50-59"
    if n > 0:
        return "49以下"
    return "不明"


def collect_role_texts_for_race(url, base_html=None, base_text=None, debug=False):
    """
    v24.1: 1レースの役割別ページを明示的に取得して、label -> text/html/url を返す。
    重要:
      race-detail: 出走表
      data: AI3連対率/AIオッズ/妙味/前づけ/決まり手率
      last-minute: 当日展示/展示ST/水面気象
      motor: モーター実績・モーター展示平均
      race-result: 結果
    """
    role = {}
    for label, u in build_race_page_urls(url):
        try:
            html = base_html if (base_html is not None and u == url) else get_html(u, "role " + label + " " + get_race_title(url), debug=False)
            if is_top_like_html(html):
                if debug:
                    print("  role除外:", label, "top/404-like")
                continue
            txt = make_soup(html, debug=False).get_text("\n", strip=True)
            role[label] = {"url": u, "html": html, "text": txt, "lines": [x.strip() for x in txt.split("\n") if x.strip()]}
            if debug:
                print("  role取得:", label, "text_len", len(txt),
                      "AI3=", "AI3連対率" in txt,
                      "妙味=", ("オッズの妙味度" in txt or "AIオッズ評価" in txt),
                      "展示=", ("展示タイム" in txt or "スタート情報" in txt),
                      "気象=", "水面気象情報" in txt)
        except Exception as e:
            role[label] = {"url": u, "html": "", "text": "", "lines": [], "error": type(e).__name__ + ": " + str(e)}
            if debug:
                print("  role失敗:", label, type(e).__name__, str(e)[:120])
    if base_text and "race-detail" not in role:
        role["race-detail"] = {"url": url, "html": base_html or "", "text": base_text, "lines": [x.strip() for x in base_text.split("\n") if x.strip()]}
    return role

def collect_deep_text_for_race(url, base_html, base_text, debug=False):
    """
    v24.1: 役割別URLの本文を結合して既存パーサーにも渡す。
    ただし実際の特徴抽出は role_lines を優先する。
    """
    role = collect_role_texts_for_race(url, base_html, base_text, debug=debug)
    chunks = []
    if base_text:
        chunks.append("\n===== DEEP_URL: base =====\n" + base_text)
    for label, item in role.items():
        txt = item.get("text", "")
        u = item.get("url", "")
        if txt:
            chunks.append("\n===== DEEP_URL: " + label + " " + u + " =====\n" + txt)
    return "\n".join(chunks)

def make_feature_audit(race_data, lines):
    r1 = get_frame(race_data, 1)
    keys = {
        "AI3連対率": r1.get("AI3連対率", ""),
        "AI予測1着率": r1.get("AI予測1着率", r1.get("AI1着率", "")),
        "1着投票率": r1.get("1着投票率", ""),
        "オッズ妙味": r1.get("オッズ妙味", ""),
        "展示タイム": r1.get("展示タイム", ""),
        "展示順位": r1.get("展示順位", ""),
        "展示ST": r1.get("展示ST", ""),
        "展示ST順位": r1.get("展示ST順位", ""),
        "進入安定度": r1.get("進入安定度", ""),
        "逃げ率": r1.get("逃げ率", ""),
    }
    ok = sum(1 for v in keys.values() if v not in [None, "", []])
    race_data["v22特徴取得数"] = ok
    race_data["v22特徴監査"] = keys
    joined = "\n".join(lines)
    race_data["v22本文キーワード"] = {
        "AI3連対率": "AI3連対率" in joined,
        "AIオッズ評価": "AIオッズ" in joined or "妙味" in joined,
        "展示": "展示" in joined,
        "スタート情報": "スタート情報" in joined,
        "前づけ": "前づけ" in joined,
        "決まり手率": "決まり手率" in joined,
    }
    return race_data


def parse_race_detail(url, include_deep=False, debug=False):
    html = get_html(url, get_race_title(url), debug=debug)
    soup = make_soup(html, debug=debug)
    text = soup.get_text("\n", strip=True)

    # v24.2: 役割別URLの本文を保持する。
    # 以前は role_lines 未定義のまま参照してバックテストが全件「?」になることがあった。
    role_lines = {"race-detail": [x.strip() for x in text.split("\n") if x.strip()]}

    if include_deep:
        texts = [text]
        for label, u in build_race_page_urls(url):
            if label == "race-detail":
                continue
            try:
                h2 = get_html(u, label, debug=debug)
                if is_top_like_html(h2):
                    if debug:
                        print("[役割別URLスキップ top_like]", label, u)
                    continue
                t2 = make_soup(h2, debug=False).get_text("\n", strip=True)
                role_lines[label] = [x.strip() for x in t2.split("\n") if x.strip()]
                # 結果ページは予想特徴量には混ぜない。結果解析は extract_result_info 側で行う。
                if label not in ["race-result", "result"]:
                    texts.append(t2)
            except Exception as e:
                role_lines[label] = []
                if debug:
                    print("[役割別URL取得失敗]", label, type(e).__name__, str(e))
        text = "\n".join(texts)

    lines = [x.strip() for x in text.split("\n") if x.strip()]

    names = []
    # 通常：/racer/リンクから取得
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "/racer/" in href:
            name = a.get_text(strip=True)
            if name and name not in names:
                names.append(name)

    # フォールバック：級別(A1/A2/B1/B2)の直前行を選手名として拾う
    if len(names) < 6:
        fallback = []
        for i, line in enumerate(lines):
            if line in ["A1", "A2", "B1", "B2"] and i >= 1:
                cand = lines[i - 1]
                if (
                    cand and cand not in fallback
                    and not re.search(r"^[0-9.％%kg歳No\-]+$", cand)
                    and cand not in ["枠", "写真", "レーサー", "早見", "節間成績"]
                    and len(cand) <= 12
                ):
                    fallback.append(cand)
        if len(fallback) >= 6:
            names = fallback[:6]

    racers = []
    for i, name in enumerate(names[:6], start=1):
        idx = text.find(name)
        next_idx = len(text)
        if i < len(names[:6]):
            found = text.find(names[i], idx + len(name))
            if found != -1:
                next_idx = found
        block = text[idx:next_idx] if idx != -1 else ""
        blines = [x.strip() for x in block.split("\n") if x.strip()]
        blines = [x for x in blines if x not in ["%", "歳", "kg", "No."]]
        joined = "\n".join(blines)

        cls = ""
        for c in ["A1", "A2", "B1", "B2"]:
            if re.search(r"(^|\n)" + c + r"($|\n)", joined):
                cls = c
                break
        if not cls and len(blines) > 1 and blines[1] in ["A1", "A2", "B1", "B2"]:
            cls = blines[1]

        data = {
            "枠": i,
            "選手名": name,
            "級別": cls,
            "平均ST": "",
            "全国勝率": "",
            "全国2連率": "",
            "全国3連率": "",
            "当地勝率": "",
            "当地2連率": "",
            "当地3連率": "",
            "モーター2連率": "",
            "モーター3連率": "",
            "F持ち": 0,
            "確認用raw": blines[:80],
        }

        fm = re.search(r"F\s*(\d+)", joined)
        data["F持ち"] = int(fm.group(1)) if fm else 0

        st = re.search(r"(?<![0-9])\.\d{2}", joined)
        data["平均ST"] = st.group(0) if st else ""

        # 小数を広めに拾う。年齢/体重も混ざるが、勝率・率の位置は概ねこの順。
        nums = re.findall(r"(?<![0-9])(?:\d+\.\d+|\.\d{2})(?![0-9])", joined)
        nums_no_st = [x for x in nums if not x.startswith(".")]
        # 体重らしき45〜60台の値が先に来るため、勝率らしい0〜10を起点にする
        start_idx = 0
        for k, v in enumerate(nums_no_st):
            try:
                fv = float(v)
                if 0 <= fv <= 10:
                    start_idx = k
                    break
            except Exception:
                pass
        vals = nums_no_st[start_idx:]
        if len(vals) >= 1: data["全国勝率"] = vals[0]
        if len(vals) >= 2: data["全国2連率"] = vals[1]
        if len(vals) >= 3: data["全国3連率"] = vals[2]
        if len(vals) >= 4: data["当地勝率"] = vals[3]
        if len(vals) >= 5: data["当地2連率"] = vals[4]
        if len(vals) >= 6: data["当地3連率"] = vals[5]

        # モーター率は No. の直後の2つの率を優先。なければ後方の値から補完。
        mm = re.search(r"No\.?\s*\n?\s*\d+\s*\n?\s*([0-9]+\.[0-9]+)\s*%?\s*\n?\s*([0-9]+\.[0-9]+)", block)
        if mm:
            data["モーター2連率"] = mm.group(1)
            data["モーター3連率"] = mm.group(2)
        elif len(vals) >= 8:
            data["モーター2連率"] = vals[-4]
            data["モーター3連率"] = vals[-3]

        racers.append(data)

    weather = extract_weather_from_text(text)
    if include_deep and role_lines.get("last-minute"):
        w2 = extract_weather_from_text("\n".join(role_lines.get("last-minute", [])))
        if any(w2.values()):
            weather = w2
    place, race_date, race_no = get_race_parts(url)
    series_day = extract_series_day(text)
    psychology = estimate_psychology_context(race_no, series_day)
    race_data = {
        "レースURL": url,
        "レース": get_race_title(url),
        "場コード": place,
        "日付": race_date,
        "レース番号": race_no_to_int(race_no),
        "レース番号カテゴリ": get_race_no_category(race_no),
        "節日": series_day,
        "心理コンテキスト": psychology,
        "解析選手数": len(racers),
        "出走表": racers,
        "選手プロファイル素材": build_player_profile_material(racers),
        "水面気象情報": weather,
        "本文先頭": lines[:80],
    }
    race_data = enrich_race_features(race_data, text, lines, role_lines=role_lines)
    race_data = make_feature_audit(race_data, lines)
    return race_data

def extract_weather_from_text(text):
    weather = {"天候": "", "風向": "", "風速": "", "波高": "", "気温": "", "水温": ""}
    patterns = {
        "天候": r"天候\s*[:：]?\s*([^\n\s]+)",
        "風向": r"風向\s*[:：]?\s*([^\n\s]+)",
        "風速": r"風速\s*[:：]?\s*([0-9.]+)\s*m",
        "波高": r"(?:波高|波の高さ)\s*[:：]?\s*([0-9.]+)\s*cm",
        "気温": r"気温\s*[:：]?\s*([0-9.]+)\s*℃",
        "水温": r"水温\s*[:：]?\s*([0-9.]+)\s*℃",
    }
    for k, p in patterns.items():
        m = re.search(p, text)
        if m:
            weather[k] = m.group(1)
    return weather


def find_related_urls(race_detail_url, debug=False):
    html = get_html(race_detail_url, get_race_title(race_detail_url) + " related", debug=debug)
    soup = make_soup(html, debug=debug)
    related = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if not href:
            continue
        full_url = urljoin(BASE_URL, href)
        if "/race/" in full_url:
            item = {"text": text, "url": full_url}
            if item not in related:
                related.append(item)
    return related


def pick_url_by_keywords(related_urls, keywords):
    for item in related_urls:
        target = item.get("text", "") + " " + item.get("url", "")
        for key in keywords:
            if key in target:
                return item["url"]
    return ""


def official_result_url_from_race_url(race_url):
    place, race_date, race_no = get_race_parts(race_url)
    jcd = JCD_MAP.get(place, "")
    rno = race_no_to_int(race_no)
    if not jcd or not race_date or not rno:
        return ""
    hd = race_date.replace("-", "")
    return f"https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd}&hd={hd}"




def parse_boaters_result_texts(texts):
    """
    v24.5: BOATERS /race-result の縦並びテキストを直接解析する。
    例:
      3連単 / 1 / 2 / 5 / 1,480 / 円 / 2
      スタート情報 ... 決まり手 ... 逃げ
      水面気象情報 ... 風速 / 5 / m/s ... 波高 / 5 / cm
    """
    result = {
        "着順": "",
        "3連単": "",
        "3連単払戻": "",
        "人気": "",
        "決まり手": "",
        "風速": "",
        "風向": "",
        "波高": "",
        "水温": "",
        "着順リスト": "",
        "結果3連単": "",
    }

    if not texts:
        return result

    texts = [unicodedata.normalize("NFKC", str(t)).strip() for t in texts if str(t).strip()]
    texts = [t.replace("三連単", "3連単") for t in texts]

    # 3連単・払戻・人気
    try:
        idx = texts.index("3連単")
        combo = str(texts[idx + 1]) + "-" + str(texts[idx + 2]) + "-" + str(texts[idx + 3])
        payout = safe_int(texts[idx + 4])
        popularity = str(texts[idx + 6]) if idx + 6 < len(texts) else ""
        if is_valid_trifecta(combo) and payout > 0:
            result["3連単"] = combo
            result["着順"] = combo
            result["結果3連単"] = combo
            result["3連単払戻"] = str(payout) + "円"
            result["人気"] = popularity
    except Exception:
        pass

    # 着順リスト。レース結果ブロックの「着」「枠」以降から、着順→枠番のペアを拾う。
    try:
        idx = texts.index("レース結果")
        order = []
        expected = "1"
        i = idx + 1
        while i < len(texts) and len(order) < 6:
            if texts[i] in ["スタート情報", "着順の記号について"]:
                break
            if texts[i] == expected and i + 1 < len(texts) and texts[i + 1] in ["1", "2", "3", "4", "5", "6"]:
                order.append(texts[i + 1])
                expected = str(len(order) + 1)
                i += 2
                continue
            i += 1
        if order:
            result["着順リスト"] = "-".join(order)
            if len(order) >= 3:
                result["結果3連単"] = "-".join(order[:3])
                if not result["3連単"]:
                    result["3連単"] = result["結果3連単"]
                    result["着順"] = result["結果3連単"]
    except Exception:
        pass

    # 決まり手。スタート情報ブロック優先。
    kimarite_list = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
    try:
        idx = texts.index("スタート情報")
        around = texts[idx:idx + 60]
    except Exception:
        around = texts
    for t in around:
        if t in kimarite_list:
            result["決まり手"] = t
            break
    if not result["決まり手"]:
        for t in texts:
            if t in kimarite_list:
                result["決まり手"] = t
                break

    # 水面気象情報
    for i, t in enumerate(texts):
        try:
            if t == "風速":
                result["風速"] = texts[i + 1] + texts[i + 2]
            elif t == "風向":
                result["風向"] = texts[i + 1]
            elif t == "波高":
                result["波高"] = texts[i + 1] + texts[i + 2]
            elif t == "水温":
                result["水温"] = texts[i + 1] + texts[i + 2]
        except Exception:
            pass

    return result


def append_prediction_history(record):
    """v24.5: 予想と結果を prediction_history.json に蓄積する。"""
    hist_file = save_path("prediction_history.json")
    try:
        if os.path.exists(hist_file) and os.path.getsize(hist_file) > 0:
            with open(hist_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        else:
            history = []
    except Exception:
        history = []

    history.append(record)
    with open(hist_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return hist_file


# ===== v25.1: prediction_history分析・共有結果出力 =====
def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def results_path(filename):
    d = ensure_dir(save_path("results"))
    return os.path.join(d, filename)


def logs_path(filename):
    d = ensure_dir(save_path("logs"))
    return os.path.join(d, filename)


def write_latest_log(message):
    """ChatGPTに渡しやすい最新ログを保存する。"""
    p = logs_path("latest.log")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat(timespec="seconds") + " " + str(message) + "\n")
    except Exception:
        pass
    return p


def parse_yen_or_number(value):
    """'1,230円' '5m' '3cm' などから整数部分を抜く。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    m = re.search(r"-?\d+", s.replace(",", ""))
    return int(m.group(0)) if m else 0


def normalize_hit(value):
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "y", "的中")


def history_bet_amount(record):
    """prediction_historyの1レコードから投資額を推定する。基本は100円/点。"""
    if "投資額" in record:
        return parse_yen_or_number(record.get("投資額"))
    bets = record.get("予想買い目", [])
    if isinstance(bets, str):
        # "1-2-3 1-3-2" のような文字列にも対応
        bet_count = len([x for x in re.split(r"[\s,、]+", bets.strip()) if x])
    elif isinstance(bets, list):
        bet_count = len(bets)
    else:
        bet_count = 0
    # Xなどで買わない運用だった場合に備える
    if record.get("安定度ランク") == "X" and record.get("回収", 0) in (0, "0", ""):
        # ただし履歴だけではskip_x判定が完全には分からないため、買い目があるなら投資した扱いにする
        pass
    return bet_count * 100


def metric_template():
    return {"件数": 0, "購入対象": 0, "投資": 0, "払戻": 0, "的中": 0}


def add_metric(bucket, record):
    bet = history_bet_amount(record)
    pay = parse_yen_or_number(record.get("回収", 0))
    hit = normalize_hit(record.get("的中", False))
    bucket["件数"] += 1
    if bet > 0:
        bucket["購入対象"] += 1
    bucket["投資"] += bet
    bucket["払戻"] += pay
    bucket["的中"] += 1 if hit else 0


def finalize_metric(bucket):
    races = bucket.get("件数", 0)
    bought = bucket.get("購入対象", 0)
    bet = bucket.get("投資", 0)
    pay = bucket.get("払戻", 0)
    hit = bucket.get("的中", 0)
    bucket["的中率"] = round(hit / races * 100, 1) if races else 0
    bucket["購入時的中率"] = round(hit / bought * 100, 1) if bought else 0
    bucket["回収率"] = round(pay / bet * 100, 1) if bet else 0
    return bucket


def wind_bucket(value):
    n = parse_yen_or_number(value)
    if n <= 2:
        return "0-2m"
    if n <= 5:
        return "3-5m"
    return "6m以上"


def wave_bucket(value):
    n = parse_yen_or_number(value)
    if n <= 2:
        return "0-2cm"
    if n <= 5:
        return "3-5cm"
    return "6cm以上"


def confidence_bucket_from_score(value):
    n = parse_yen_or_number(value)
    if n >= 80:
        return "80以上"
    if n >= 70:
        return "70-79"
    if n >= 60:
        return "60-69"
    if n >= 50:
        return "50-59"
    if n > 0:
        return "49以下"
    return "不明"


def water_type_for_place(place_code):
    code = normalize_place_code(place_code) if "normalize_place_code" in globals() else (place_code or "")
    return WATER_TYPE_MAP.get(code, "不明")


def race_time_bucket_from_no(race_no):
    n = parse_yen_or_number(race_no)
    if n <= 0:
        return "不明"
    if n <= 4:
        return "序盤(1-4R)"
    if n <= 8:
        return "中盤(5-8R)"
    return "終盤(9-12R)"


def roi_value(metric):
    return float(metric.get("回収率", 0) or 0)


def build_roi_ranking(group_data, min_count=10, limit=30):
    items = []
    for key, v in group_data.items():
        if v.get("件数", 0) >= min_count:
            items.append({
                "値": key,
                "件数": v.get("件数", 0),
                "的中率": v.get("的中率", 0),
                "回収率": v.get("回収率", 0),
                "投資": v.get("投資", 0),
                "払戻": v.get("払戻", 0),
            })
    items.sort(key=lambda x: (x.get("回収率", 0), x.get("件数", 0)), reverse=True)
    return items[:limit]


def add_combo_metric(groups, group_name, key, record):
    if not key or "不明" in str(key):
        return
    if group_name not in groups:
        groups[group_name] = {}
    if key not in groups[group_name]:
        groups[group_name][key] = metric_template()
    add_metric(groups[group_name][key], record)


def build_prediction_history_summary(history):
    """v25.3: prediction_history.jsonから条件抽出用の集計を作る。"""
    total = metric_template()
    groups = {
        "信頼度ランク別": {},
        "1号艇信頼度帯別": {},
        "場別": {},
        "水質別": {},
        "レース時間帯別": {},
        "風速帯別": {},
        "波高帯別": {},
        "決まり手別": {},
        "人気別": {},
        "場×1号艇信頼度帯": {},
        "水質×1号艇信頼度帯": {},
        "場×レース時間帯": {},
        "水質×レース時間帯": {},
        "風速帯×1号艇信頼度帯": {},
    }

    for rec in history:
        if not isinstance(rec, dict):
            continue
        add_metric(total, rec)

        place = rec.get("場コード") or "不明"
        water = water_type_for_place(place)
        confidence = rec.get("1号艇信頼度帯") or confidence_bucket_from_score(rec.get("1号艇信頼度"))
        race_time = rec.get("レース番号カテゴリ") or race_time_bucket_from_no(rec.get("レース番号"))
        wind = wind_bucket(rec.get("風速") or rec.get("結果風速"))
        wave = wave_bucket(rec.get("波高") or rec.get("結果波高"))

        keys = {
            "信頼度ランク別": rec.get("安定度ランク") or "不明",
            "1号艇信頼度帯別": confidence,
            "場別": place,
            "水質別": water,
            "レース時間帯別": race_time,
            "風速帯別": wind,
            "波高帯別": wave,
            "決まり手別": rec.get("決まり手") or "不明",
            "人気別": str(rec.get("人気") or "不明"),
            "場×1号艇信頼度帯": f"{place}×{confidence}",
            "水質×1号艇信頼度帯": f"{water}×{confidence}",
            "場×レース時間帯": f"{place}×{race_time}",
            "水質×レース時間帯": f"{water}×{race_time}",
            "風速帯×1号艇信頼度帯": f"{wind}×{confidence}",
        }
        for gname, key in keys.items():
            if not key or "不明" in str(key):
                continue
            if key not in groups[gname]:
                groups[gname][key] = metric_template()
            add_metric(groups[gname][key], rec)

    summary = {
        "version": "v25.6",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "prediction_history.json",
        "保存先": SAVE_DIR,
        "全体": finalize_metric(total),
        "分析": {},
        "ROIランキング": {},
        "推奨条件": [],
        "次アクション": [],
    }

    for gname, data in groups.items():
        finalized = {k: finalize_metric(v) for k, v in data.items()}
        summary["分析"][gname] = dict(sorted(finalized.items(), key=lambda kv: kv[1].get("件数", 0), reverse=True))
        summary["ROIランキング"][gname] = build_roi_ranking(finalized, min_count=10, limit=20)

    # v25.3: 実用候補。複合条件は偶然値を減らすため件数15以上、単独条件は件数20以上。
    candidates = []
    for gname, data in summary["分析"].items():
        is_combo = "×" in gname
        min_count = 15 if is_combo else 20
        for key, v in data.items():
            if v.get("件数", 0) >= min_count and v.get("回収率", 0) >= 110:
                candidates.append({
                    "条件": gname,
                    "値": key,
                    "件数": v["件数"],
                    "的中率": v["的中率"],
                    "回収率": v["回収率"],
                    "投資": v.get("投資", 0),
                    "払戻": v.get("払戻", 0),
                })
    candidates.sort(key=lambda x: (x["回収率"], x["件数"]), reverse=True)
    summary["推奨条件"] = candidates[:30]
    summary["高回収候補"] = candidates[:30]  # 互換用

    if not history:
        summary["次アクション"].append("prediction_history.jsonが空です。まずモード5/6で20件以上バックテストしてください。")
    elif summary["全体"].get("件数", 0) < 100:
        summary["次アクション"].append("まだ件数が少ないです。最低100件、理想400件まで増やしてから補正を強めます。")
    else:
        summary["次アクション"].append("推奨条件の件数と回収率を確認し、v25.4で推奨条件のみ購入するフィルタバックテストへ進みます。")
    return summary

def load_prediction_history():
    p = save_path("prediction_history.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        write_latest_log("prediction_history読込失敗: " + type(e).__name__ + " " + str(e))
        return []


def save_prediction_history_summary():
    """results/backtest_summary.json と prediction_history_summary.json の両方に保存。"""
    history = load_prediction_history()
    summary = build_prediction_history_summary(history)
    p1 = results_path("backtest_summary.json")
    p2 = save_path("prediction_history_summary.json")
    for p in (p1, p2):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    write_latest_log("prediction_history分析保存: " + p1)
    return summary, p1, p2


def run_history_analysis():
    summary, p1, p2 = save_prediction_history_summary()
    print("\n===== v25.3 prediction_history分析 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("✅ results保存:", p1)
    print("✅ 互換保存:", p2)
    print("✅ ログ:", logs_path("latest.log"))


def extract_result_info(race_url, debug=False):
    """
    v20: 結果取得を24場向けに強化。
    - 公式BOATRACE結果ページを最優先
    - BOATERSは race-result を result より先に見る
    - 失敗理由を候補URLごとに残す
    - 3連単と払戻が両方取れた時だけ結果採用
    """
    candidates = []

    official = official_result_url_from_race_url(race_url)
    if official:
        candidates.append(("official", official))

    # BOATERSは /result では404になり、/race-result が正の場があるので先に見る
    candidates.append(("boaters_race_result", race_url.replace("race-detail", "race-result")))
    candidates.append(("boaters_result", race_url.replace("race-detail", "result")))

    try:
        related = find_related_urls(race_url, debug=False)
        u = pick_url_by_keywords(related, ["結果", "払戻", "リプレイ", "race-result", "result", "payoff"])
        if u:
            candidates.append(("related", u))
    except Exception:
        pass

    # 最後の保険。detailに結果情報が含まれるケースだけ拾える。
    candidates.append(("detail", race_url))

    seen = set()
    errors = []

    for label, u in candidates:
        if not u or u in seen:
            continue
        seen.add(u)
        try:
            html = get_html(u, "result " + label + " " + get_race_title(race_url), debug=debug)
            soup = make_soup(html, debug=False)
            text = soup.get_text("\n", strip=True)

            if debug:
                print("[結果候補]", label, u, "len=", len(text), "sig=", html_sig(text))
                print(text[:800])

            texts = []
            for t in text.split("\n"):
                t = t.strip()
                if t:
                    texts.append(t)

            # v24.5: BOATERS /race-result は縦並びテキストを直接解析する。
            if "boaters" in label or "related" in label or "detail" in label:
                result = parse_boaters_result_texts(texts)
                fallback = parse_result_text(text)
                for k, v in fallback.items():
                    if not result.get(k) and v:
                        result[k] = v
            else:
                result = parse_result_text(text)

            result["結果URL"] = u
            result["結果取得エラー"] = ""

            # 3連単と払戻が両方取れている場合だけ採用する。
            if result.get("3連単") and safe_int(result.get("3連単払戻", "")) > 0:
                return result

            errors.append(label + ": 抽出不可")
        except Exception as e:
            errors.append(label + ": " + type(e).__name__ + ": " + str(e))
            continue

    r = parse_result_text("")
    r["結果URL"] = ""
    r["結果取得エラー"] = " / ".join(errors) if errors else "結果を抽出できず"
    return r


def normalize_result_text(text):
    """全角数字/記号を半角化して結果抽出しやすくする。"""
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = t.replace("−", "-").replace("－", "-").replace("ー", "-")
    t = t.replace("￥", "円").replace("¥", "円")
    # よくある表記ゆれ
    t = t.replace("三連単", "3連単")
    t = t.replace("三連複", "3連複")
    return t


def is_valid_trifecta(value):
    """3連単として有効かチェック。1-1-2のような重複は無効。"""
    if not value:
        return False
    parts = str(value).split("-")
    if len(parts) != 3:
        return False
    if any(x not in ["1", "2", "3", "4", "5", "6"] for x in parts):
        return False
    return len(set(parts)) == 3


def set_trifecta_if_valid(result, parts, payout=""):
    """候補の3艇を検証して、有効で払戻もある時だけresultへ入れる。"""
    try:
        nums = [str(x).strip() for x in parts]
        value = "-".join(nums)
        payout_num = safe_int(payout)
        if is_valid_trifecta(value) and payout_num > 0:
            result["3連単"] = value
            result["3連単払戻"] = str(payout_num) + "円"
            result["着順"] = value
            return True
    except Exception:
        pass
    return False


def find_first_payout_in_segment(segment):
    """3連単ブロック内から払戻金を探す。金額と円/人気が別行でも対応。"""
    ignore_small_words = {"1", "2", "3", "4", "5", "6", "人気", "組番", "払戻金", "払戻", "円"}

    for idx, raw in enumerate(segment):
        x = str(raw).strip()
        if not x:
            continue

        # 710円 / 71,790 円 / 円710
        m = re.search(r"([0-9][0-9,]*)\s*円", x)
        if m:
            val = safe_int(m.group(1))
            if val >= 100:
                return str(val)

        m = re.search(r"円\s*([0-9][0-9,]*)", x)
        if m:
            val = safe_int(m.group(1))
            if val >= 100:
                return str(val)

        # 710 / 円 のように分割されるケース
        if re.fullmatch(r"[0-9][0-9,]*", x) and x not in ignore_small_words:
            val = safe_int(x)
            if val >= 100:
                # 次行が円、または直前/周辺に払戻文言があれば採用
                next_line = str(segment[idx + 1]).strip() if idx + 1 < len(segment) else ""
                prev_line = str(segment[idx - 1]).strip() if idx - 1 >= 0 else ""
                near = " ".join(str(y) for y in segment[max(0, idx-3):idx+4])
                if next_line == "円" or prev_line == "払戻金" or prev_line == "払戻" or "払戻" in near:
                    return str(val)

    return ""


def _line_is_payout_start(x):
    x = str(x).strip()
    if re.search(r"[0-9][0-9,]*\s*円", x):
        return True
    if x == "円":
        return True
    # 100以上の単独数字は払戻金候補。艇番の1〜6とは分ける。
    if re.fullmatch(r"[0-9][0-9,]*", x) and safe_int(x) >= 100:
        return True
    return False


def parse_result_text(text):
    """
    結果ページから3連単/払戻/決まり手を抽出する。
    v20:
    - 払戻が取れない結果は採用しない
    - 1-1-2等の不正組番を無効化
    - トップ/一覧ページ由来の誤抽出を防止
    - 公式/BOATERSの縦並びに対応
    """
    result = {"着順": "", "3連単": "", "3連単払戻": "", "人気": "", "決まり手": "", "風速": "", "風向": "", "波高": "", "水温": "", "着順リスト": "", "結果3連単": ""}
    joined = normalize_result_text(text)
    lines = [x.strip() for x in joined.split("\n") if x.strip()]

    if "3連単" not in joined:
        return result

    for p in [
        r"決まり手\s*[:：]?\s*(逃げ|差し|まくり差し|まくり|抜き|恵まれ)",
        r"(逃げ|差し|まくり差し|まくり|抜き|恵まれ)",
    ]:
        m = re.search(p, joined)
        if m:
            result["決まり手"] = m.group(1)
            break

    # 1) 横並び・ハイフン表記: 3連単 1-2-3 1,230円
    patterns = [
        r"3連単\s*([1-6])\s*[-]\s*([1-6])\s*[-]\s*([1-6]).{0,260}?([0-9][0-9,]*)\s*円",
        r"3連単\s*([1-6]{3}).{0,260}?([0-9][0-9,]*)\s*円",
    ]
    for ptn in patterns:
        m = re.search(ptn, joined, re.S)
        if not m:
            continue
        if len(m.groups()) == 4:
            if set_trifecta_if_valid(result, [m.group(1), m.group(2), m.group(3)], m.group(4)):
                return result
        elif len(m.groups()) == 2:
            s3 = m.group(1)
            if set_trifecta_if_valid(result, [s3[0], s3[1], s3[2]], m.group(2)):
                return result

    # 2) 縦並び。3連単ブロック内だけを見る。
    for i, line in enumerate(lines):
        if "3連単" not in line:
            continue

        segment = []
        for x in lines[i:i + 80]:
            if x != line and ("3連複" in x or "2連単" in x or "2連複" in x or "拡連複" in x or "単勝" in x):
                break
            segment.append(x)

        payout = find_first_payout_in_segment(segment)
        if not payout:
            continue

        # 2-1) 同一行に 1-2-3 がある
        for x in segment:
            mm = re.search(r"([1-6])\s*-\s*([1-6])\s*-\s*([1-6])", x)
            if mm and set_trifecta_if_valid(result, [mm.group(1), mm.group(2), mm.group(3)], payout):
                return result

        # 2-2) 3連単行の後ろから、払戻金エリア前までの単独艇番を候補にする。
        digits = []
        for x in segment[1:]:
            x = str(x).strip()
            if _line_is_payout_start(x) or "払戻" in x or "人気" in x:
                break
            if re.fullmatch(r"[1-6]", x):
                digits.append(x)

        if len(digits) >= 3 and set_trifecta_if_valid(result, digits[:3], payout):
            return result

        for k in range(0, max(0, len(digits) - 2)):
            if set_trifecta_if_valid(result, digits[k:k+3], payout):
                return result

    return result

def is_valid_race_detail_html(html, place_code, date_str, rno, debug=False):
    """
    BOATERSは存在しない/未公開URLでもHTTP200でトップ/一覧HTMLを返すことがある。
    その誤追加を防ぐため、対象レースのdetail本文らしさを厳しめに判定する。
    """
    if not html or len(html) < 10000:
        return False, "HTMLが短い"

    place_jp = PLACE_MAP_REV.get(place_code, place_code)
    race_no = str(rno) + "R"
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)

    # トップ/一覧ページだけが返っている典型パターン
    top_markers = [
        "レース検索",
        "レースアイコンの説明",
        "本日",
        "のレース",
        "無料公開レース",
    ]
    detail_markers = [
        "枠", "レーサー", "全国勝率", "当地勝率", "モーター", "ボート", "平均ST"
    ]

    # HTML内に対象URLや対象レース番号/場名が全く無ければNG
    target_path = f"/race/{place_code}/{date_str}/{race_no}/race-detail"
    has_target_path = target_path in html
    has_place = place_jp in text
    has_race_no = race_no in text or (str(rno) in text and "R" in text)
    marker_count = sum(1 for m in detail_markers if m in text)
    racer_link_count = html.count('/racer/')

    # 明らかにトップページで、detail要素がない
    if marker_count < 3 and racer_link_count < 3:
        if any(m in text for m in top_markers):
            return False, f"トップ/一覧ページっぽい marker={marker_count} racer_links={racer_link_count}"

    # しっかり6選手分取れそうならOK
    if racer_link_count >= 6 and marker_count >= 3:
        return True, f"detail判定OK racer_links={racer_link_count} marker={marker_count}"

    # racerリンクが少なくてもdetail指標が十分ならOK
    if has_place and has_race_no and marker_count >= 5:
        return True, f"text判定OK marker={marker_count} racer_links={racer_link_count}"

    # 対象パスがHTML内にあり、detail指標もそこそこあるならOK
    if has_target_path and marker_count >= 3:
        return True, f"target_path判定OK marker={marker_count} racer_links={racer_link_count}"

    return False, f"detail判定NG place={has_place} race={has_race_no} marker={marker_count} racer_links={racer_link_count}"

def get_recent_completed_race_urls(place_code, base_date_str, limit=20, max_days=45, debug=True):
    urls = []

    # v25.3: 場コードが空/未知のままURL生成される事故を防止
    place_code = normalize_place_code(place_code)
    if not place_code:
        print("❌ place_code が空または未知です。URL探索を中止します。")
        return []

    try:
        base_dt = datetime.strptime(base_date_str, "%Y-%m-%d") if base_date_str else datetime.now()
    except Exception:
        base_dt = datetime.now()

    print("\n直近終了済みレースURLを探索します...")
    print("場:", PLACE_MAP_REV.get(place_code, place_code), "/ 目標件数:", limit, "/ 最大探索日数:", max_days)
    print("v17: トップ誤取得除外＋1レースごと保存＋再開対応。")

    prev_sigs = {}
    for day_back in range(0, max_days):
        d = base_dt - timedelta(days=day_back)
        date_str = d.strftime("%Y-%m-%d")
        print("探索中:", date_str, "現在", len(urls), "/", limit)
        for rno in range(12, 0, -1):
            if len(urls) >= limit:
                return urls

            # v25.3: 念のためURL作成直前にもガード
            if not place_code:
                print(f"⚠️ 場コードなしのためスキップ: {date_str} {rno}R")
                continue

            url = f"{BASE_URL}/race/{place_code}/{date_str}/{rno}R/race-detail"
            label = f"{PLACE_MAP_REV.get(place_code, place_code)} {date_str} {rno}R"
            print("  [探索]", label)
            try:
                html = get_html(url, label, debug=True)
                sig = html_sig(html)
                if sig in prev_sigs:
                    print("  ⚠️ 同じHTML署名:", sig, "前回=", prev_sigs[sig], "今回=", label)
                else:
                    prev_sigs[sig] = label

                low = html.lower()
                if "404" in low and len(html) < 50000:
                    print("  なし判定: 404っぽい")
                    continue
                if "not found" in low and len(html) < 50000:
                    print("  なし判定: not foundっぽい")
                    continue

                ok, reason = is_valid_race_detail_html(html, place_code, date_str, rno, debug=debug)
                if not ok:
                    print("  なし/未公開判定:", reason, "len=", len(html), "sig=", sig)
                    continue

                urls.append(url)
                print("  追加", len(urls), ":", label, "(", reason, ") len=", len(html), "sig=", sig)
            except Exception as e:
                print("  取得失敗:", type(e).__name__, str(e))
                continue
    return urls


def score_race_stability(race_data):
    # 安定度：イン信頼とは分離。天候/構成/F/B2を主に見る。
    score = 100
    reasons = []
    racers = race_data.get("出走表", [])
    weather = race_data.get("水面気象情報", {})

    wind = safe_float(weather.get("風速")) or 0
    wave = safe_float(weather.get("波高")) or 0
    if wind >= 6:
        score -= 20; reasons.append("風速6m以上")
    elif wind >= 4:
        score -= 10; reasons.append("風速4m以上")
    if wave >= 5:
        score -= 15; reasons.append("波高5cm以上")
    elif wave >= 3:
        score -= 5; reasons.append("波高3cm以上")

    a_count = 0
    b2_count = 0
    f_count = 0
    for r in racers:
        cls = str(r.get("級別", ""))
        if cls in ["A1", "A2"]:
            a_count += 1
        if cls == "B2":
            b2_count += 1
        f_count += int(r.get("F持ち", 0) or 0)

    if len(racers) >= 6:
        if a_count <= 1:
            score -= 12; reasons.append("A級少なめ")
        if b2_count >= 2:
            score -= 12; reasons.append("B2が2艇以上")
        if f_count >= 2:
            score -= 12; reasons.append("F持ち複数")
        elif f_count == 1:
            score -= 5; reasons.append("F持ちあり")
    else:
        score -= 60; reasons.append("出走表解析不足")

    score = max(0, min(100, score))
    rank = "S" if score >= 80 else "A" if score >= 65 else "B" if score >= 50 else "X"
    return {"安定度スコア": score, "安定度ランク": rank, "安定度理由": reasons}


def score_inner_confidence(race_data):
    racers = race_data.get("出走表", [])
    if not racers:
        return {"1号艇信頼度": 0, "理由": ["出走表なし"]}
    r1 = racers[0]
    score = 50
    reasons = []
    cls = str(r1.get("級別", ""))
    if cls == "A1": score += 15; reasons.append("1号艇A1")
    elif cls == "A2": score += 8; reasons.append("1号艇A2")
    elif cls == "B2": score -= 12; reasons.append("1号艇B2")

    st = safe_float(r1.get("平均ST"))
    if st is not None:
        if st <= 0.14: score += 10; reasons.append("1号艇平均ST良")
        elif st >= 0.18: score -= 8; reasons.append("1号艇平均ST遅め")

    motor = safe_float(r1.get("モーター2連率"))
    if motor is not None:
        if motor >= 40: score += 10; reasons.append("1号艇モーター上位")
        elif motor < 30: score -= 8; reasons.append("1号艇モーター弱め")

    # v21: AI/妙味/展示/進入/決まり手を反映
    ai3 = safe_float(r1.get("AI3連対率"))
    if ai3 is not None:
        if ai3 >= 85: score += 8; reasons.append("AI3連対率85以上")
        elif ai3 < 50: score -= 8; reasons.append("AI3連対率50未満")

    ai1 = safe_float(r1.get("AI予測1着率")) or safe_float(r1.get("AI1着率"))
    if ai1 is not None:
        if ai1 >= 70: score += 10; reasons.append("AI予測1着率70以上")
        elif ai1 >= 55: score += 5; reasons.append("AI予測1着率55以上")
        elif ai1 < 20: score -= 8; reasons.append("AI予測1着率20未満")

    edge = safe_float(r1.get("AI妙味差"))
    if edge is not None:
        if edge >= 5: score += 4; reasons.append("AI妙味差プラス")
        elif edge <= -5: score -= 4; reasons.append("AI妙味差マイナス")

    ex_rank = safe_int(r1.get("展示順位"))
    ex_diff = safe_float(r1.get("展示差"))
    if ex_rank:
        if ex_rank == 1: score += 5; reasons.append("展示1位")
        elif ex_rank == 2: score += 3; reasons.append("展示2位")
        elif ex_rank >= 5: score -= 3; reasons.append("展示5位以下")
    if ex_diff is not None:
        if ex_diff >= 0.08: score -= 10; reasons.append("展示差0.08以上")
        elif ex_diff >= 0.05: score -= 5; reasons.append("展示差0.05以上")

    st_rank = safe_int(r1.get("展示ST順位"))
    if st_rank:
        if st_rank <= 2: score += 4; reasons.append("展示ST上位")
        elif st_rank >= 5: score -= 4; reasons.append("展示ST下位")

    entry = safe_float(r1.get("進入安定度"))
    if entry is not None:
        if entry >= 90: score += 5; reasons.append("進入安定90%以上")
        elif entry < 70: score -= 5; reasons.append("進入不安")

    escape = safe_float(r1.get("逃げ率"))
    if escape is not None:
        if escape >= 65: score += 6; reasons.append("逃げ率65%以上")
        elif escape < 40: score -= 6; reasons.append("逃げ率40%未満")

    score = max(0, min(100, round(score)))
    return {"1号艇信頼度": score, "理由": reasons}



# ===== v25.6: Explainableログ（分析用） =====
def _num_or_blank(v):
    x = safe_float(v)
    return "" if x is None else x


def build_explainable_boat_scores(race_data):
    """
    予想精度を直接変えず、分析用に各艇の評価内訳を保存する。
    EngineA: 期待値・展開変化寄り（逃げ崩れ/穴検知の研究用）
    EngineB: 的中率・堅さ寄り（今後の本命モデル研究用）
    """
    racers = race_data.get("出走表", []) or []
    weather = race_data.get("水面気象情報", {}) or {}
    wind = safe_float(weather.get("風速")) or 0
    wave = safe_float(weather.get("波高")) or 0

    # 相対評価用
    vals = []
    for r in racers:
        vals.append({
            "枠": safe_int(r.get("枠")),
            "平均ST": safe_float(r.get("平均ST")),
            "モーター2連率": safe_float(r.get("モーター2連率")),
            "ボート2連率": safe_float(r.get("ボート2連率")),
            "展示タイム": safe_float(r.get("展示タイム")),
            "AI3連対率": safe_float(r.get("AI3連対率")),
            "AI予測1着率": safe_float(r.get("AI予測1着率")) or safe_float(r.get("AI1着率")),
        })
    best_st = min([v["平均ST"] for v in vals if v["平均ST"] is not None], default=None)
    best_motor = max([v["モーター2連率"] for v in vals if v["モーター2連率"] is not None], default=None)
    best_boat = max([v["ボート2連率"] for v in vals if v["ボート2連率"] is not None], default=None)
    best_ex = min([v["展示タイム"] for v in vals if v["展示タイム"] is not None], default=None)

    logs = []
    for r in racers:
        frame = safe_int(r.get("枠"))
        comp = {}
        reasons = []

        def add(name, a_pt, b_pt, reason=""):
            comp[name] = {"A": round(a_pt, 2), "B": round(b_pt, 2), "理由": reason}
            if reason:
                reasons.append(reason)

        cls = str(r.get("級別", ""))
        if cls == "A1": add("級別", 8, 10, f"{frame}号艇A1")
        elif cls == "A2": add("級別", 5, 7, f"{frame}号艇A2")
        elif cls == "B2": add("級別", -6, -8, f"{frame}号艇B2")
        else: add("級別", 0, 0, "")

        st = safe_float(r.get("平均ST"))
        if st is not None:
            if st <= 0.13: add("平均ST", 10, 8, f"平均ST優秀({st})")
            elif st <= 0.16: add("平均ST", 5, 5, f"平均ST良({st})")
            elif st >= 0.19: add("平均ST", -6, -6, f"平均ST遅め({st})")
            else: add("平均ST", 0, 0, "")
        else: add("平均ST", 0, 0, "平均ST欠損")

        motor = safe_float(r.get("モーター2連率"))
        if motor is not None:
            rel = (motor - best_motor) if best_motor is not None else 0
            if motor >= 45: add("モーター", 10, 8, f"モーター強({motor}%)")
            elif motor >= 40: add("モーター", 7, 5, f"モーター上位({motor}%)")
            elif motor < 30: add("モーター", -5, -5, f"モーター弱({motor}%)")
            else: add("モーター", 0, 0, "")
            comp["モーター"]["平均との差"] = round(rel, 2) if rel is not None else ""
        else: add("モーター", 0, 0, "モーター欠損")

        boat = safe_float(r.get("ボート2連率"))
        if boat is not None:
            rel = (boat - best_boat) if best_boat is not None else 0
            if boat >= 45: add("ボート", 6, 5, f"ボート強({boat}%)")
            elif boat < 30: add("ボート", -3, -3, f"ボート弱({boat}%)")
            else: add("ボート", 0, 0, "")
            comp["ボート"]["平均との差"] = round(rel, 2) if rel is not None else ""
        else: add("ボート", 0, 0, "ボート欠損")

        national = safe_float(r.get("全国勝率"))
        if national is not None:
            if national >= 6.5: add("全国勝率", 5, 7, f"全国勝率高({national})")
            elif national < 4.5: add("全国勝率", -3, -5, f"全国勝率低({national})")
            else: add("全国勝率", 0, 0, "")
        else: add("全国勝率", 0, 0, "全国勝率欠損")

        local = safe_float(r.get("当地勝率"))
        if local is not None:
            if local >= 6.5: add("当地勝率", 4, 6, f"当地勝率高({local})")
            elif local < 4.5: add("当地勝率", -2, -4, f"当地勝率低({local})")
            else: add("当地勝率", 0, 0, "")
        else: add("当地勝率", 0, 0, "当地勝率欠損")

        ai3 = safe_float(r.get("AI3連対率"))
        if ai3 is not None:
            if ai3 >= 85: add("AI3連対率", 8, 12, f"AI3連対率高({ai3}%)")
            elif ai3 < 50: add("AI3連対率", -4, -8, f"AI3連対率低({ai3}%)")
            else: add("AI3連対率", 0, 0, "")
        else: add("AI3連対率", 0, 0, "AI3連対率欠損")

        ai1 = safe_float(r.get("AI予測1着率")) or safe_float(r.get("AI1着率"))
        if ai1 is not None:
            if ai1 >= 65: add("AI1着率", 8, 14, f"AI1着率高({ai1}%)")
            elif ai1 < 20: add("AI1着率", -4, -8, f"AI1着率低({ai1}%)")
            else: add("AI1着率", 0, 0, "")
        else: add("AI1着率", 0, 0, "AI1着率欠損")

        ex = safe_float(r.get("展示タイム"))
        ex_rank = safe_int(r.get("展示順位"))
        if ex is not None:
            diff = (ex - best_ex) if best_ex is not None else 0
            if ex_rank == 1 or diff <= 0.00: add("展示タイム", 8, 10, f"展示上位({ex})")
            elif diff >= 0.08: add("展示タイム", -5, -7, f"展示差大({round(diff,2)})")
            elif diff >= 0.05: add("展示タイム", -2, -3, f"展示差あり({round(diff,2)})")
            else: add("展示タイム", 0, 0, "")
            comp["展示タイム"]["平均との差"] = round(diff, 2)
            comp["展示タイム"]["順位"] = ex_rank or ""
        else: add("展示タイム", 0, 0, "展示タイム欠損")

        ex_st_rank = safe_int(r.get("展示ST順位"))
        ex_st = r.get("展示ST", "")
        if ex_st_rank:
            if ex_st_rank <= 2: add("展示ST", 6, 6, f"展示ST上位({ex_st_rank}位)")
            elif ex_st_rank >= 5: add("展示ST", -4, -5, f"展示ST下位({ex_st_rank}位)")
            else: add("展示ST", 0, 0, "")
        else: add("展示ST", 0, 0, "展示ST欠損")

        edge = safe_float(r.get("AI妙味差"))
        if edge is not None:
            if edge >= 5: add("AI妙味", 8, 1, f"妙味プラス({edge})")
            elif edge <= -5: add("AI妙味", -4, 0, f"妙味マイナス({edge})")
            else: add("AI妙味", 0, 0, "")
        else: add("AI妙味", 0, 0, "AI妙味欠損")

        # 展開変化の研究用。外枠の伸び/荒天はAにだけやや加点。
        rough_pt = 0
        if frame in [3, 4, 5] and wind >= 4:
            rough_pt += 4
        if frame in [3, 4, 5] and wave >= 3:
            rough_pt += 3
        if frame == 1 and (wind >= 5 or wave >= 4):
            rough_pt -= 4
        add("気象展開補正", rough_pt, 0, f"風{wind}m 波{wave}cm 補正" if rough_pt else "")

        a_score = 50 + sum(v.get("A", 0) for v in comp.values())
        b_score = 50 + sum(v.get("B", 0) for v in comp.values())
        a_score = max(0, min(100, round(a_score, 2)))
        b_score = max(0, min(100, round(b_score, 2)))
        logs.append({
            "枠": frame,
            "選手名": r.get("選手名", ""),
            "級別": r.get("級別", ""),
            "EngineA点": a_score,
            "EngineB点": b_score,
            "評価内訳": comp,
            "主要理由": reasons[:8],
            "元データ": {
                "平均ST": _num_or_blank(r.get("平均ST")),
                "全国勝率": _num_or_blank(r.get("全国勝率")),
                "当地勝率": _num_or_blank(r.get("当地勝率")),
                "モーター2連率": _num_or_blank(r.get("モーター2連率")),
                "ボート2連率": _num_or_blank(r.get("ボート2連率")),
                "展示タイム": _num_or_blank(r.get("展示タイム")),
                "展示順位": safe_int(r.get("展示順位")) or "",
                "展示ST": r.get("展示ST", ""),
                "展示ST順位": safe_int(r.get("展示ST順位")) or "",
                "AI3連対率": _num_or_blank(r.get("AI3連対率")),
                "AI1着率": _num_or_blank(r.get("AI予測1着率")) or _num_or_blank(r.get("AI1着率")),
                "投票率": _num_or_blank(r.get("1着投票率")),
                "AI妙味差": _num_or_blank(r.get("AI妙味差")),
            }
        })

    logs.sort(key=lambda x: x.get("枠", 0))
    return logs


def build_bet_reason_logs(bets, boat_logs, race_data, race_style):
    by_frame = {safe_int(b.get("枠")): b for b in boat_logs}
    a_rank = {b.get("枠"): i + 1 for i, b in enumerate(sorted(boat_logs, key=lambda x: x.get("EngineA点", 0), reverse=True))}
    b_rank = {b.get("枠"): i + 1 for i, b in enumerate(sorted(boat_logs, key=lambda x: x.get("EngineB点", 0), reverse=True))}
    out = []
    for i, bet in enumerate(bets, start=1):
        frames = [safe_int(x) for x in str(bet).split("-") if str(x).strip()]
        reasons = []
        total_a = 0
        total_b = 0
        for f in frames:
            bl = by_frame.get(f, {})
            total_a += safe_float(bl.get("EngineA点")) or 0
            total_b += safe_float(bl.get("EngineB点")) or 0
            if bl:
                rs = bl.get("主要理由", [])[:3]
                if rs:
                    reasons.append(f"{f}号艇: " + " / ".join(rs))
        out.append({
            "順位": i,
            "買い目": bet,
            "EngineA合計": round(total_a, 2),
            "EngineB合計": round(total_b, 2),
            "A順位組合せ": [a_rank.get(f, "") for f in frames],
            "B順位組合せ": [b_rank.get(f, "") for f in frames],
            "採用理由": reasons,
            "レース分類": (race_style or {}).get("レース分類", ""),
            "分類理由": (race_style or {}).get("分類理由", []),
        })
    return out


def build_miss_reason_log(pred, result, race_data):
    trifecta = result.get("3連単", "") or result.get("結果3連単", "")
    bets = pred.get("買い目", []) or []
    if not trifecta:
        return {"判定": "結果なし", "理由": ["3連単結果を取得できず"]}
    if trifecta in bets:
        return {"判定": "的中", "理由": ["結果買い目が予想買い目に含まれる"]}
    first, second, third = split_trifecta(trifecta)
    reasons = []
    inner = safe_float((pred.get("イン信頼") or {}).get("1号艇信頼度"))
    if first != "1":
        reasons.append(f"逃げ崩れ: 1着が{first}号艇")
        if inner is not None and inner >= 65:
            reasons.append(f"1号艇信頼度高め({inner})だったが逃げ失敗")
    else:
        reasons.append("逃げ決着を取り切れず")
    kimarite = result.get("決まり手", "")
    if kimarite:
        reasons.append(f"決まり手={kimarite}")
    weather = race_data.get("水面気象情報", {}) or {}
    wind = result.get("風速", "") or weather.get("風速", "")
    wave = result.get("波高", "") or weather.get("波高", "")
    if safe_float(wind) is not None and safe_float(wind) >= 4:
        reasons.append(f"風速高め({wind}m)")
    if safe_float(wave) is not None and safe_float(wave) >= 3:
        reasons.append(f"波高高め({wave}cm)")
    pred_firsts = [str(b).split("-")[0] for b in bets if b]
    if first and first not in pred_firsts:
        reasons.append(f"1着候補漏れ: {first}号艇を頭で買っていない")
    return {
        "判定": "外れ",
        "結果": trifecta,
        "予想買い目": bets,
        "理由": reasons,
    }



# ===== BARP v25.7 / EXP-0001: EngineB・Meta予想ログ =====
def _unique_bets(seq, limit=None):
    out = []
    seen = set()
    for b in seq or []:
        b = str(b).strip()
        if not is_valid_trifecta(b):
            continue
        if b not in seen:
            seen.add(b)
            out.append(b)
        if limit and len(out) >= limit:
            break
    return out


def build_engine_b_bets_from_scores(boat_logs, inner_score=None, max_bets=8):
    """
    EngineB: 的中率重視モデルの初期仮説。
    EngineAは変更せず、艇別EngineB点から本命寄りの買い目を別系統で作る。
    目的は「A/B/Metaの比較ログ」を残し、後続分析に使うこと。
    """
    ranked = [x for x in sorted(boat_logs or [], key=lambda z: z.get("EngineB点", 0), reverse=True) if safe_int(x.get("枠"))]
    frames = [str(safe_int(x.get("枠"))) for x in ranked]
    if len(frames) < 3:
        return []

    bets = []
    top = frames[:4]
    head = top[0]
    seconds = [f for f in top if f != head]
    thirds = [f for f in frames[:5] if f != head]

    # 1号艇信頼度が高い時は1頭本命を優先。ただしB点1位が別ならB点1位も残す。
    if inner_score is not None and safe_float(inner_score) is not None and safe_float(inner_score) >= 65 and "1" in frames[:3]:
        head_candidates = ["1"]
        if head != "1":
            head_candidates.append(head)
    else:
        head_candidates = [head]
        # Bは的中率重視だが、2番手頭まで少し許容して取りこぼしを確認する。
        if len(top) > 1:
            head_candidates.append(top[1])

    for h in head_candidates:
        sec_pool = [f for f in frames[:4] if f != h]
        third_pool = [f for f in frames[:5] if f != h]
        for s2 in sec_pool[:3]:
            for s3 in third_pool[:4]:
                if len({h, s2, s3}) == 3:
                    bets.append(f"{h}-{s2}-{s3}")
                    if len(_unique_bets(bets)) >= max_bets:
                        return _unique_bets(bets, max_bets)
    return _unique_bets(bets, max_bets)


def build_meta_bets(engine_a_bets, engine_b_bets, max_bets=8):
    """
    Meta初期版: A/Bの一致買い目を最優先し、次にA・Bの上位を混ぜる。
    ここでは資金配分までは固定せず、比較検証用の買い目を保存する。
    """
    a = _unique_bets(engine_a_bets or [])
    b = _unique_bets(engine_b_bets or [])
    common = [x for x in a if x in set(b)]
    mixed = common + a[:4] + b[:4] + a[4:] + b[4:]
    return _unique_bets(mixed, max_bets)


def build_engine_compare_record(engine_a_bets, engine_b_bets, meta_bets, result):
    trifecta = result.get("3連単", "") if isinstance(result, dict) else ""
    payout = safe_int(result.get("3連単払戻", "")) if isinstance(result, dict) else 0
    out = {}
    for name, bets in [("A", engine_a_bets), ("B", engine_b_bets), ("Meta", meta_bets)]:
        bets = _unique_bets(bets or [])
        hit = bool(trifecta and trifecta in bets)
        out[name] = {
            "買い目": bets,
            "点数": len(bets),
            "投資額": len(bets) * 100,
            "的中": hit,
            "回収": payout if hit else 0,
        }
    return out

def make_machine_bets(race_data):
    # v21: 的中予想の前に「硬い/中間/穴狙い」を分類して買い目を分岐。
    stability = score_race_stability(race_data)
    inner = score_inner_confidence(race_data)
    inner_score = inner["1号艇信頼度"]
    race_style = classify_race_style(race_data, inner_score, stability["安定度スコア"])
    style = race_style["レース分類"]

    if style == "硬め本線":
        bets = ["1-3-4", "1-4-3", "1-3-2", "1-2-3", "1-4-2", "1-2-4"]
    elif style == "穴狙い":
        bets = ["3-1-2", "3-2-1", "4-1-3", "4-3-1", "1-3-2", "1-4-3", "5-3-1", "3-5-1"]
    else:
        if inner_score >= 70:
            bets = ["1-2-3", "1-3-2", "1-3-4", "1-4-3", "1-2-4", "1-4-2"]
        elif inner_score >= 55:
            bets = ["1-3-2", "3-1-2", "1-2-3", "3-2-1", "1-3-4", "3-1-4", "4-1-3", "1-4-3"]
        else:
            bets = ["3-1-2", "3-2-1", "4-1-3", "4-3-1", "1-3-2", "1-4-3", "5-3-1", "3-5-1"]

    # v25.6: 予想ロジックは変えず、分析用の評価内訳だけ追加保存する。
    boat_logs = build_explainable_boat_scores(race_data)
    bet_reason_logs = build_bet_reason_logs(bets, boat_logs, race_data, race_style)

    # BARP v25.7 / EXP-0001:
    # EngineAは現行買い目そのもの。EngineBとMetaは比較検証用に追加保存する。
    engine_a_bets = list(bets)
    engine_b_bets = build_engine_b_bets_from_scores(boat_logs, inner_score=inner_score, max_bets=8)
    meta_bets = build_meta_bets(engine_a_bets, engine_b_bets, max_bets=8)
    engine_b_reason_logs = build_bet_reason_logs(engine_b_bets, boat_logs, race_data, race_style)
    meta_reason_logs = build_bet_reason_logs(meta_bets, boat_logs, race_data, race_style)

    return {
        "安定度": stability,
        "イン信頼": inner,
        "レース分類": race_style,
        "買い目": bets,
        "点数": len(bets),
        "艇別評価ログ": boat_logs,
        "買い目理由ログ": bet_reason_logs,
        "EngineA買い目": engine_a_bets,
        "EngineB買い目": engine_b_bets,
        "Meta買い目": meta_bets,
        "EngineB買い目理由ログ": engine_b_reason_logs,
        "Meta買い目理由ログ": meta_reason_logs,
    }



def get_csv_fieldnames():
    return [
        "レース", "URL", "場コード", "日付", "レース番号", "レース番号カテゴリ", "節日",
        "心理タグ", "心理スコア",
        "解析選手数", "安定度ランク", "安定度スコア", "1号艇信頼度", "1号艇信頼度帯",
        "レース分類", "硬さ指数", "荒れ指数", "分類理由",
        "1号艇AI3連対率", "1号艇AI予測1着率", "1号艇投票率", "1号艇妙味差", "1号艇オッズ妙味",
        "1号艇展示タイム", "1号艇展示順位", "1号艇展示差", "展示レンジ", "1号艇展示ST", "1号艇展示ST順位",
        "1号艇進入安定度", "1号艇逃げ率",
        "v22特徴取得数", "v22本文キーワード", "v22特徴監査", "v241取得元監査",
        "予想買い目", "投資額",
        "EngineA買い目", "EngineA点数", "EngineA投資額", "EngineA的中", "EngineA回収",
        "EngineB買い目", "EngineB点数", "EngineB投資額", "EngineB的中", "EngineB回収",
        "Meta買い目", "Meta点数", "Meta投資額", "Meta的中", "Meta回収",
        "艇別評価ログ", "買い目理由ログ", "EngineB買い目理由ログ", "Meta買い目理由ログ", "外れ理由ログ",
        "EngineA艇順位", "EngineB艇順位", "EngineA平均点", "EngineB平均点",
        "結果", "1着艇", "2着艇", "3着艇",
        "払戻", "人気", "的中", "回収", "決まり手", "結果URL", "結果3連単", "着順リスト",
        "結果風速", "結果風向", "結果波高", "結果水温",
        "選手プロファイル素材", "結果取得エラー", "エラー"
    ]


def load_processed_urls(csv_file):
    processed = set()
    if not os.path.exists(csv_file):
        return processed
    try:
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                u = row.get("URL", "")
                if u:
                    processed.add(u)
    except Exception as e:
        print("既存CSV読込失敗:", type(e).__name__, str(e))
    return processed


def append_csv_row(csv_file, row, fieldnames=None):
    """1レースごとに追記保存。途中で落ちてもここまで残る。"""
    if fieldnames is None:
        fieldnames = get_csv_fieldnames()
    file_exists = os.path.exists(csv_file) and os.path.getsize(csv_file) > 0
    clean_row = {k: row.get(k, "") for k in fieldnames}
    with open(csv_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(clean_row)


def read_rows_from_csv(csv_file):
    rows = []
    if not os.path.exists(csv_file):
        return rows
    try:
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
    except Exception as e:
        print("既存CSV再読込失敗:", type(e).__name__, str(e))
    return rows


def build_summary(rows):
    total_bet = sum(safe_int(r.get("投資額", 0)) for r in rows)
    total_pay = sum(safe_int(r.get("回収", 0)) for r in rows)
    hit_count = sum(safe_int(r.get("的中", 0)) for r in rows)
    roi = round(total_pay / total_bet * 100, 1) if total_bet else 0
    summary = {
        "version": "v25.6",
        "対象レース数": len(rows),
        "購入対象レース数": sum(1 for r in rows if safe_int(r.get("投資額", 0)) > 0),
        "的中数": hit_count,
        "投資合計": total_bet,
        "払戻合計": total_pay,
        "回収率": str(roi) + "%" if total_bet else "0%",
        "保存先": SAVE_DIR,
    }
    by_rank = {}
    for r in rows:
        rk = r.get("安定度ランク", "?") or "?"
        if rk not in by_rank:
            by_rank[rk] = {"件数": 0, "投資": 0, "払戻": 0, "的中": 0}
        by_rank[rk]["件数"] += 1
        by_rank[rk]["投資"] += safe_int(r.get("投資額", 0))
        by_rank[rk]["払戻"] += safe_int(r.get("回収", 0))
        by_rank[rk]["的中"] += safe_int(r.get("的中", 0))
    for rk, v in by_rank.items():
        v["回収率"] = str(round(v["払戻"] / v["投資"] * 100, 1)) + "%" if v["投資"] else "-"
    summary["ランク別"] = by_rank

    def group_summary(key_or_func):
        groups = {}
        for r in rows:
            if callable(key_or_func):
                k = key_or_func(r)
            else:
                k = r.get(key_or_func, "")
            k = k or "不明"
            if k == "不明":
                continue
            if k not in groups:
                groups[k] = {"件数": 0, "投資": 0, "払戻": 0, "的中": 0}
            groups[k]["件数"] += 1
            groups[k]["投資"] += safe_int(r.get("投資額", 0))
            groups[k]["払戻"] += safe_int(r.get("回収", 0))
            groups[k]["的中"] += safe_int(r.get("的中", 0))
        for k, v in groups.items():
            v["回収率"] = str(round(v["払戻"] / v["投資"] * 100, 1)) + "%" if v["投資"] else "-"
            v["的中率"] = str(round(v["的中"] / v["件数"] * 100, 1)) + "%" if v["件数"] else "-"
        return dict(sorted(groups.items(), key=lambda kv: kv[1].get("件数", 0), reverse=True))

    def pct_to_float(v):
        try:
            return float(str(v).replace("%", ""))
        except Exception:
            return 0.0

    def ranking(data, min_count=10, limit=20):
        arr = []
        for k, v in data.items():
            if v.get("件数", 0) >= min_count:
                item = dict(v)
                item["値"] = k
                item["回収率数値"] = pct_to_float(v.get("回収率"))
                arr.append(item)
        arr.sort(key=lambda x: (x.get("回収率数値", 0), x.get("件数", 0)), reverse=True)
        return arr[:limit]

    summary["1号艇信頼度帯別"] = group_summary("1号艇信頼度帯")
    summary["レース番号カテゴリ別"] = group_summary("レース番号カテゴリ")
    summary["レース時間帯別"] = group_summary(lambda r: race_time_bucket_from_no(r.get("レース番号")))
    summary["節日別"] = group_summary("節日")
    summary["場別"] = group_summary("場コード")
    summary["水質別"] = group_summary(lambda r: water_type_for_place(r.get("場コード")))
    summary["決まり手別"] = group_summary("決まり手")
    summary["結果風速別"] = group_summary("結果風速")
    summary["結果風速帯別"] = group_summary(lambda r: wind_bucket(r.get("結果風速")))
    summary["結果波高別"] = group_summary("結果波高")
    summary["結果波高帯別"] = group_summary(lambda r: wave_bucket(r.get("結果波高")))
    summary["人気別"] = group_summary("人気")
    summary["レース分類別"] = group_summary("レース分類")
    summary["1号艇展示順位別"] = group_summary("1号艇展示順位")
    summary["1号艇オッズ妙味別"] = group_summary("1号艇オッズ妙味")

    # v25.3: 複合条件。買わないレースを削るための候補抽出に使う。
    summary["場×1号艇信頼度帯"] = group_summary(lambda r: f"{r.get('場コード') or '不明'}×{r.get('1号艇信頼度帯') or confidence_bucket_from_score(r.get('1号艇信頼度'))}")
    summary["水質×1号艇信頼度帯"] = group_summary(lambda r: f"{water_type_for_place(r.get('場コード'))}×{r.get('1号艇信頼度帯') or confidence_bucket_from_score(r.get('1号艇信頼度'))}")
    summary["場×レース時間帯"] = group_summary(lambda r: f"{r.get('場コード') or '不明'}×{race_time_bucket_from_no(r.get('レース番号'))}")
    summary["水質×レース時間帯"] = group_summary(lambda r: f"{water_type_for_place(r.get('場コード'))}×{race_time_bucket_from_no(r.get('レース番号'))}")
    summary["風速帯×1号艇信頼度帯"] = group_summary(lambda r: f"{wind_bucket(r.get('結果風速'))}×{r.get('1号艇信頼度帯') or confidence_bucket_from_score(r.get('1号艇信頼度'))}")

    rank_targets = [
        "場別", "水質別", "1号艇信頼度帯別", "レース時間帯別", "場×1号艇信頼度帯",
        "水質×1号艇信頼度帯", "場×レース時間帯", "水質×レース時間帯", "風速帯×1号艇信頼度帯"
    ]
    summary["ROIランキング"] = {k: ranking(summary.get(k, {}), min_count=10, limit=20) for k in rank_targets}

    candidates = []
    for k in rank_targets:
        min_count = 15 if "×" in k else 20
        for item in ranking(summary.get(k, {}), min_count=min_count, limit=50):
            if item.get("回収率数値", 0) >= 110:
                candidates.append({
                    "条件": k,
                    "値": item.get("値"),
                    "件数": item.get("件数"),
                    "的中率": item.get("的中率"),
                    "回収率": item.get("回収率"),
                    "投資": item.get("投資"),
                    "払戻": item.get("払戻"),
                })
    candidates.sort(key=lambda x: (float(str(x.get("回収率", "0")).replace("%", "")), x.get("件数", 0)), reverse=True)
    summary["推奨条件"] = candidates[:30]
    return summary

def save_summary_json(json_file, rows):
    summary = build_summary(rows)
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # v25.1: ChatGPTと共有しやすい固定パスにも保存する。
    try:
        ensure_dir(save_path("results"))
        shared_file = results_path("backtest_summary.json")
        with open(shared_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        write_latest_log("backtest_summary保存: " + shared_file)
    except Exception as e:
        print("  ⚠️ results/backtest_summary.json保存失敗:", type(e).__name__, str(e))

    # v25.1: prediction_history.jsonがあれば履歴分析サマリも更新する。
    try:
        if os.path.exists(save_path("prediction_history.json")):
            save_prediction_history_summary()
    except Exception as e:
        print("  ⚠️ prediction_history分析保存失敗:", type(e).__name__, str(e))
    return summary

def backtest_urls(urls, skip_x=True, include_deep=False, filename_prefix="backtest"):
    csv_file = save_path(filename_prefix + ".csv")
    json_file = save_path(filename_prefix + "_summary.json")
    fieldnames = get_csv_fieldnames()

    processed_urls = load_processed_urls(csv_file)
    rows = read_rows_from_csv(csv_file)

    print("\n===== バックテスト開始 =====")
    print("対象URL数:", len(urls), "skip_x=", skip_x, "include_deep=", include_deep)
    print("CSV:", csv_file)
    print("JSON:", json_file)
    if processed_urls:
        print("再開モード: 既存CSVから", len(processed_urls), "件をスキップ対象にします")

    try:
        for idx, url in enumerate(urls, start=1):
            title = get_race_title(url)
            if url in processed_urls:
                print("\n[", idx, "/", len(urls), "]", title, "→ 既存CSVにあるためスキップ")
                continue

            print("\n[", idx, "/", len(urls), "]", title)
            row = {"レース": title, "URL": url}
            try:
                race_data = parse_race_detail(url, include_deep=include_deep, debug=False)
                pred = make_machine_bets(race_data)
                result = extract_result_info(url, debug=False)

                if idx == 1 and (race_data.get("解析選手数", 0) == 0 or not result.get("3連単")):
                    print("  ⚠️ 初回レースの解析/結果が空なので原因調査用ダンプを保存します")
                    dump_debug_pages(url, "debug_first_backtest")

                bets = pred["買い目"]
                rank = pred["安定度"]["安定度ランク"]
                do_bet = not (skip_x and rank == "X")
                bet_amount = len(bets) * 100 if do_bet else 0
                trifecta = result.get("3連単", "")
                payout = safe_int(result.get("3連単払戻", ""))
                hit = do_bet and trifecta in bets
                pay = payout if hit else 0
                engine_compare = build_engine_compare_record(
                    pred.get("EngineA買い目", bets),
                    pred.get("EngineB買い目", []),
                    pred.get("Meta買い目", []),
                    result
                )

                first, second, third = split_trifecta(trifecta)
                psych = race_data.get("心理コンテキスト", {}) or {}
                inner_score = pred["イン信頼"]["1号艇信頼度"]
                row.update({
                    "場コード": race_data.get("場コード", ""),
                    "日付": race_data.get("日付", ""),
                    "レース番号": race_data.get("レース番号", ""),
                    "レース番号カテゴリ": race_data.get("レース番号カテゴリ", ""),
                    "節日": race_data.get("節日", ""),
                    "心理タグ": " ".join(psych.get("心理タグ", [])),
                    "心理スコア": psych.get("心理スコア", 0),
                    "解析選手数": race_data.get("解析選手数", 0),
                    "安定度ランク": rank,
                    "安定度スコア": pred["安定度"]["安定度スコア"],
                    "1号艇信頼度": inner_score,
                    "1号艇信頼度帯": inner_confidence_bucket(inner_score),
                    "レース分類": pred.get("レース分類", {}).get("レース分類", ""),
                    "硬さ指数": pred.get("レース分類", {}).get("硬さ指数", ""),
                    "荒れ指数": pred.get("レース分類", {}).get("荒れ指数", ""),
                    "分類理由": " ".join(pred.get("レース分類", {}).get("分類理由", [])),
                    "1号艇AI3連対率": get_frame(race_data, 1).get("AI3連対率", ""),
                    "1号艇AI予測1着率": get_frame(race_data, 1).get("AI予測1着率", get_frame(race_data, 1).get("AI1着率", "")),
                    "1号艇投票率": get_frame(race_data, 1).get("1着投票率", ""),
                    "1号艇妙味差": get_frame(race_data, 1).get("AI妙味差", ""),
                    "1号艇オッズ妙味": get_frame(race_data, 1).get("オッズ妙味", ""),
                    "1号艇展示タイム": get_frame(race_data, 1).get("展示タイム", ""),
                    "1号艇展示順位": get_frame(race_data, 1).get("展示順位", ""),
                    "1号艇展示差": get_frame(race_data, 1).get("展示差", ""),
                    "展示レンジ": (race_data.get("展示サマリ", {}) or {}).get("展示レンジ", ""),
                    "1号艇展示ST": get_frame(race_data, 1).get("展示ST", ""),
                    "1号艇展示ST順位": get_frame(race_data, 1).get("展示ST順位", ""),
                    "1号艇進入安定度": get_frame(race_data, 1).get("進入安定度", ""),
                    "1号艇逃げ率": get_frame(race_data, 1).get("逃げ率", ""),
                    "v22特徴取得数": race_data.get("v22特徴取得数", ""),
                    "v22本文キーワード": json.dumps(race_data.get("v22本文キーワード", {}), ensure_ascii=False),
                    "v22特徴監査": json.dumps(race_data.get("v22特徴監査", {}), ensure_ascii=False),
                    "予想買い目": " ".join(bets),
                    "投資額": bet_amount,
                    "EngineA買い目": " ".join(engine_compare["A"]["買い目"]),
                    "EngineA点数": engine_compare["A"]["点数"],
                    "EngineA投資額": engine_compare["A"]["投資額"] if do_bet else 0,
                    "EngineA的中": "1" if (do_bet and engine_compare["A"]["的中"]) else "0",
                    "EngineA回収": engine_compare["A"]["回収"] if do_bet else 0,
                    "EngineB買い目": " ".join(engine_compare["B"]["買い目"]),
                    "EngineB点数": engine_compare["B"]["点数"],
                    "EngineB投資額": engine_compare["B"]["投資額"] if do_bet else 0,
                    "EngineB的中": "1" if (do_bet and engine_compare["B"]["的中"]) else "0",
                    "EngineB回収": engine_compare["B"]["回収"] if do_bet else 0,
                    "Meta買い目": " ".join(engine_compare["Meta"]["買い目"]),
                    "Meta点数": engine_compare["Meta"]["点数"],
                    "Meta投資額": engine_compare["Meta"]["投資額"] if do_bet else 0,
                    "Meta的中": "1" if (do_bet and engine_compare["Meta"]["的中"]) else "0",
                    "Meta回収": engine_compare["Meta"]["回収"] if do_bet else 0,
                    "艇別評価ログ": json.dumps(pred.get("艇別評価ログ", []), ensure_ascii=False, separators=(",", ":")),
                    "買い目理由ログ": json.dumps(pred.get("買い目理由ログ", []), ensure_ascii=False, separators=(",", ":")),
                    "EngineB買い目理由ログ": json.dumps(pred.get("EngineB買い目理由ログ", []), ensure_ascii=False, separators=(",", ":")),
                    "Meta買い目理由ログ": json.dumps(pred.get("Meta買い目理由ログ", []), ensure_ascii=False, separators=(",", ":")),
                    "外れ理由ログ": json.dumps(build_miss_reason_log(pred, result, race_data), ensure_ascii=False, separators=(",", ":")),
                    "EngineA艇順位": " ".join([str(x.get("枠")) for x in sorted(pred.get("艇別評価ログ", []), key=lambda z: z.get("EngineA点", 0), reverse=True)]),
                    "EngineB艇順位": " ".join([str(x.get("枠")) for x in sorted(pred.get("艇別評価ログ", []), key=lambda z: z.get("EngineB点", 0), reverse=True)]),
                    "EngineA平均点": round(sum([safe_float(x.get("EngineA点")) or 0 for x in pred.get("艇別評価ログ", [])]) / max(1, len(pred.get("艇別評価ログ", []))), 2),
                    "EngineB平均点": round(sum([safe_float(x.get("EngineB点")) or 0 for x in pred.get("艇別評価ログ", [])]) / max(1, len(pred.get("艇別評価ログ", []))), 2),
                    "結果": trifecta,
                    "1着艇": first,
                    "2着艇": second,
                    "3着艇": third,
                    "払戻": payout,
                    "人気": result.get("人気", ""),
                    "的中": "1" if hit else "0",
                    "回収": pay,
                    "決まり手": result.get("決まり手", ""),
                    "結果URL": result.get("結果URL", ""),
                    "結果3連単": result.get("結果3連単", trifecta),
                    "着順リスト": result.get("着順リスト", ""),
                    "結果風速": result.get("風速", ""),
                    "結果風向": result.get("風向", ""),
                    "結果波高": result.get("波高", ""),
                    "結果水温": result.get("水温", ""),
                    "選手プロファイル素材": json.dumps(race_data.get("選手プロファイル素材", []), ensure_ascii=False),
                    "結果取得エラー": result.get("結果取得エラー", ""),
                    "エラー": "",
                })

                # v24.5: 予想と結果を蓄積。あとで自己学習・条件別分析に使う。
                try:
                    history_record = {
                        "保存日時": datetime.now().isoformat(timespec="seconds"),
                        "レース": title,
                        "URL": url,
                        "場コード": row.get("場コード", ""),
                        "日付": row.get("日付", ""),
                        "レース番号": row.get("レース番号", ""),
                        "安定度ランク": rank,
                        "1号艇信頼度": inner_score,
                        "1号艇信頼度帯": row.get("1号艇信頼度帯", ""),
                        "予想買い目": bets,
                        "EngineA買い目": pred.get("EngineA買い目", bets),
                        "EngineB買い目": pred.get("EngineB買い目", []),
                        "Meta買い目": pred.get("Meta買い目", []),
                        "EngineA的中": bool(do_bet and engine_compare["A"]["的中"]),
                        "EngineB的中": bool(do_bet and engine_compare["B"]["的中"]),
                        "Meta的中": bool(do_bet and engine_compare["Meta"]["的中"]),
                        "EngineA回収": engine_compare["A"]["回収"] if do_bet else 0,
                        "EngineB回収": engine_compare["B"]["回収"] if do_bet else 0,
                        "Meta回収": engine_compare["Meta"]["回収"] if do_bet else 0,
                        "艇別評価ログ": pred.get("艇別評価ログ", []),
                        "買い目理由ログ": pred.get("買い目理由ログ", []),
                        "EngineB買い目理由ログ": pred.get("EngineB買い目理由ログ", []),
                        "Meta買い目理由ログ": pred.get("Meta買い目理由ログ", []),
                        "外れ理由ログ": build_miss_reason_log(pred, result, race_data),
                        "EngineA艇順位": row.get("EngineA艇順位", ""),
                        "EngineB艇順位": row.get("EngineB艇順位", ""),
                        "結果": trifecta,
                        "払戻": payout,
                        "人気": result.get("人気", ""),
                        "的中": bool(hit),
                        "回収": pay,
                        "決まり手": result.get("決まり手", ""),
                        "風速": result.get("風速", ""),
                        "風向": result.get("風向", ""),
                        "波高": result.get("波高", ""),
                        "水温": result.get("水温", ""),
                        "結果URL": result.get("結果URL", ""),
                    }
                    append_prediction_history(history_record)
                except Exception as hist_e:
                    print("  ⚠️ prediction_history保存失敗:", type(hist_e).__name__, str(hist_e))

                print("  rank=", rank, "inner=", row["1号艇信頼度"], "result=", trifecta, "hit=", hit, "pay=", pay, "kimarite=", result.get("決まり手", ""), "wind=", result.get("風速", ""), "wave=", result.get("波高", ""))
            except Exception as e:
                row.update({
                    "解析選手数": "", "安定度ランク": "?", "安定度スコア": "", "1号艇信頼度": "",
                    "予想買い目": "", "投資額": 0,
                    "EngineA買い目": "", "EngineA点数": "", "EngineA投資額": 0, "EngineA的中": "0", "EngineA回収": 0,
                    "EngineB買い目": "", "EngineB点数": "", "EngineB投資額": 0, "EngineB的中": "0", "EngineB回収": 0,
                    "Meta買い目": "", "Meta点数": "", "Meta投資額": 0, "Meta的中": "0", "Meta回収": 0,
                    "艇別評価ログ": "", "買い目理由ログ": "", "EngineB買い目理由ログ": "", "Meta買い目理由ログ": "", "外れ理由ログ": "",
                    "EngineA艇順位": "", "EngineB艇順位": "", "EngineA平均点": "", "EngineB平均点": "",
                    "結果": "", "払戻": 0, "人気": "",
                    "的中": "0", "回収": 0, "決まり手": "", "結果URL": "", "結果3連単": "", "着順リスト": "",
                    "結果風速": "", "結果風向": "", "結果波高": "", "結果水温": "", "結果取得エラー": "",
                    "エラー": type(e).__name__ + ": " + str(e),
                })
                print("  エラー:", row["エラー"])

            rows.append(row)
            processed_urls.add(url)

            # 1レースごとに追記保存
            append_csv_row(csv_file, row, fieldnames)
            print("  ✅ 1件追記保存")

            # 10件ごとに集計JSON更新
            if len(rows) % 10 == 0:
                summary = save_summary_json(json_file, rows)
                print("  ✅ 中間集計JSON保存", "回収率=", summary.get("回収率"))

    except KeyboardInterrupt:
        print("\n⚠️ 中断されました。ここまでのCSVは保存済みです。集計JSONを更新します。")

    # 最終集計
    summary = save_summary_json(json_file, rows)

    print("\n===== 集計 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("✅ CSV保存:", csv_file)
    print("✅ JSON保存:", json_file)
    return rows, summary


def run_recent_backtest(place_code="mikuni"):
    place_code = normalize_place_code(place_code)
    if not place_code:
        print("❌ 対象場が不正です。例: 三国 / mikuni / 唐津 / karatsu")
        return [], {}

    print("\n===== 直近Nレース バックテスト =====")
    print("対象場:", PLACE_MAP_REV.get(place_code, place_code), "/ code:", place_code)
    base_date = input("基準日 YYYY-MM-DD（空欄なら今日）: ").strip()
    limit_s = input("検証件数（まず20推奨 / 空欄なら100）: ").strip()
    max_days_s = input("最大探索日数（空欄なら45日）: ").strip()
    skip_x_s = input("Xランク見送りを反映しますか？ y/n（おすすめ y）: ").strip().lower()
    deep_s = input("詳細取得しますか？ y/n（初回は高速n推奨）: ").strip().lower()

    limit = int(limit_s) if limit_s.isdigit() else 100
    max_days = int(max_days_s) if max_days_s.isdigit() else 45
    skip_x = skip_x_s != "n"
    include_deep = deep_s == "y"

    urls = get_recent_completed_race_urls(place_code, base_date, limit=limit, max_days=max_days, debug=True)
    prefix = f"backtest_{place_code}_recent_{len(urls)}"
    return backtest_urls(urls, skip_x=skip_x, include_deep=include_deep, filename_prefix=prefix)




# v23: requestsで取得したHTML/テキストに、BOATERS独自情報が存在するかを確認する監査。
def keyword_presence_report(url):
    keywords = [
        "AI3連対率",
        "AIオッズ評価",
        "オッズの妙味度",
        "AI予測",
        "1着投票率",
        "展示順位",
        "展示タイム",
        "スタート情報",
        "先頭艇別連対率",
        "決まり手率",
        "前づけデータ",
        "水面気象情報",
    ]

    candidates = []
    # v24: 役割別URLを明示監査。/data がAI3連対率・AIオッズ評価の本命。
    for label, u in build_race_page_urls(url):
        candidates.append((label, u))

    seen = set()
    report = []
    print("\n===== v23 HTMLキーワード監査 =====")
    for label, u in candidates:
        if not u or u in seen:
            continue
        seen.add(u)
        item = {"label": label, "url": u}
        try:
            html = get_html(u, "keyword " + label, debug=True)
            soup = make_soup(html, debug=False)
            text = soup.get_text("\n", strip=True)
            item["status"] = "OK"
            item["html_len"] = len(html)
            item["text_len"] = len(text)
            item["sig"] = html_sig(html)
            item["is_top_like"] = is_top_like_html(html)
            item["html_keywords"] = {k: (k in html) for k in keywords}
            item["text_keywords"] = {k: (k in text) for k in keywords}

            print("\n---", label, "---")
            print("URL:", u)
            print("html_len:", item["html_len"], "text_len:", item["text_len"], "sig:", item["sig"], "top_like:", item["is_top_like"])
            for k in keywords:
                print(k, "HTML=", item["html_keywords"][k], "TEXT=", item["text_keywords"][k])

            # 代表キーワードの周辺テキストを少しだけ出す
            for key in ["AI3連対率", "AIオッズ評価", "オッズの妙味度", "展示タイム", "スタート情報", "決まり手率"]:
                pos = text.find(key)
                if pos >= 0:
                    snippet = text[max(0, pos-120):pos+360]
                    print("\n[周辺テキスト]", key)
                    print(snippet[:600])
                    break
        except Exception as e:
            item["status"] = "ERROR"
            item["error"] = type(e).__name__ + ": " + str(e)
            print("\n---", label, "---")
            print("URL:", u)
            print("ERROR:", item["error"])
        report.append(item)

    out_path = save_path("keyword_audit_result.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("\n✅ キーワード監査JSON保存:", out_path)
    except Exception as e:
        print("監査JSON保存失敗:", type(e).__name__, str(e))
    return report



def normalize_place_code(value):
    """v25.3: 日本語場名/英字場コードを安全に英字コードへ正規化する。
    空文字・未知コードは None を返し、/race//... の生成を防ぐ。
    """
    raw = (value or "").strip()
    if not raw:
        return None

    code = PLACE_MAP.get(raw, raw)
    code = (code or "").strip()

    if not code:
        return None

    if code not in PLACE_MAP_REV:
        print("⚠️ 未知の場名/場コードのため除外:", raw, "→", code)
        return None

    return code


def parse_place_codes_input(place_input):
    """v25.3: メニュー6で複数場をカンマ区切り指定。空/未知コードは除外する。"""
    q = (place_input or "").strip()
    if not q:
        return []

    if q.lower() in ["all", "ぜんぶ", "全部", "24場"]:
        seen = []
        for code in PLACE_MAP.values():
            code = normalize_place_code(code)
            if code and code not in seen:
                seen.append(code)
        return seen

    parts = re.split(r"[,，、\s]+", q)
    codes = []
    for part in parts:
        code = normalize_place_code(part)
        if not code:
            continue
        if code not in codes:
            codes.append(code)

    if not codes:
        print("❌ 有効な場コードがありません。例: 三国 / mikuni / 唐津 / karatsu / all")

    return codes


def run_recent_backtest_multi(place_codes):
    """v25.3: 複数場を順番実行。空/未知コードを除外してから実行。"""
    normalized = []
    for c in (place_codes or []):
        code = normalize_place_code(c)
        if code and code not in normalized:
            normalized.append(code)
    place_codes = normalized

    if not place_codes:
        print("対象場がありません")
        return [], {}

    print("\n===== 複数場 直近Nレース バックテスト =====")
    print("対象場:", ", ".join(PLACE_MAP_REV.get(c, c) for c in place_codes))
    base_date = input("基準日 YYYY-MM-DD（空欄なら今日）: ").strip()
    limit_s = input("各場の検証件数（まず20推奨 / 空欄なら100）: ").strip()
    max_days_s = input("最大探索日数（空欄なら45日）: ").strip()
    skip_x_s = input("Xランク見送りを反映しますか？ y/n（おすすめ y）: ").strip().lower()
    deep_s = input("詳細取得しますか？ y/n（v24.2検証はy推奨）: ").strip().lower()

    limit = int(limit_s) if limit_s.isdigit() else 100
    max_days = int(max_days_s) if max_days_s.isdigit() else 45
    skip_x = skip_x_s != "n"
    include_deep = deep_s == "y"

    all_rows = []
    place_summaries = {}
    for code in place_codes:
        print("\n==============================")
        print("開始:", PLACE_MAP_REV.get(code, code))
        print("==============================")
        urls = get_recent_completed_race_urls(code, base_date, limit=limit, max_days=max_days, debug=True)
        prefix = f"backtest_{code}_recent_{len(urls)}"
        rows, summary = backtest_urls(urls, skip_x=skip_x, include_deep=include_deep, filename_prefix=prefix)
        all_rows.extend(rows)
        place_summaries[code] = summary

    combined = build_summary(all_rows)
    combined["場別サマリ"] = place_summaries
    combined_path = save_path("backtest_multi_recent_summary.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print("\n===== 複数場合算 =====")
    print(json.dumps(combined, ensure_ascii=False, indent=2))
    print("✅ 複数場合算JSON保存:", combined_path)
    return all_rows, combined

def run_keyword_audit():
    url = input("監査するrace-detail URLを入力してください: ").strip()
    if not url:
        print("URLなし")
        return
    keyword_presence_report(url)



def normalize_to_race_detail_url(url):
    """BOATERSの役割別URLを race-detail URL に戻す。"""
    if not url:
        return url
    suffixes = [
        "data", "odds", "last-minute", "waku", "motor",
        "race-result", "result", "race-detail"
    ]
    for suf in suffixes:
        tail = "/" + suf
        if url.endswith(tail):
            return url[: -len(suf)] + "race-detail"
    return url

def dump_debug_pages(url, prefix="debug_first_failed"):
    """
    解析できない時に、取得HTML/テキストの先頭を保存して原因を見える化する。

    v24.3:
    Gemini等でDOM構造分析しやすいように、役割別ページも必ず保存する。
      /data        -> debug_first_backtest_data.html
      /last-minute -> debug_first_backtest_last_minute.html
      /motor       -> debug_first_backtest_motor.html
    """
    paths = []

    # 入力URLが /data /motor 等でも、基準URLを /race-detail に正規化してから派生URLを作る
    base_url = normalize_to_race_detail_url(url)

    candidates = [
        ("detail", base_url),
        ("data", base_url.replace("race-detail", "data")),
        ("last_minute", base_url.replace("race-detail", "last-minute")),
        ("motor", base_url.replace("race-detail", "motor")),
        ("odds", base_url.replace("race-detail", "odds")),
        ("waku", base_url.replace("race-detail", "waku")),
    ]

    official = official_result_url_from_race_url(base_url)
    if official:
        candidates.append(("official_result", official))
    candidates.extend([
        ("boaters_result", base_url.replace("race-detail", "result")),
        ("boaters_race_result", base_url.replace("race-detail", "race-result")),
    ])
    seen = set()
    for label, u in candidates:
        if not u or u in seen:
            continue
        seen.add(u)
        try:
            html = get_html(u, "dump " + label, debug=True)
            soup = make_soup(html, debug=False)
            text = soup.get_text("\n", strip=True)
            base = prefix + "_" + label
            html_path = save_path(base + ".html")
            txt_path = save_path(base + ".txt")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html[:300000])
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("URL: " + u + "\n")
                f.write("HTML_LEN: " + str(len(html)) + "\n")
                f.write("SIG: " + html_sig(html) + "\n\n")
                f.write(text[:300000])
            paths.append(txt_path)
            print("  ダンプ保存:", txt_path)
        except Exception as e:
            print("  ダンプ失敗:", label, type(e).__name__, str(e))
    return paths


def run_url_test():
    url = input("レースURLを入力してください: ").strip()
    if not url:
        print("URLなし")
        return
    print("\n===== URL診断 =====")
    data = parse_race_detail(url, include_deep=True, debug=True)
    result = extract_result_info(url, debug=True)
    print("\n===== 取得ページダンプ =====")
    dump_debug_pages(url, "debug_url_test")
    pred = make_machine_bets(data)
    out = {"race_data": data, "result": result, "machine_prediction": pred}
    p = save_path("url_test_result.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"レース": data.get("レース"), "解析選手数": data.get("解析選手数"), "結果": result, "予測": pred}, ensure_ascii=False, indent=2))
    print("✅ 保存:", p)



# ============================================================
# v25.4 オーケストレーター機能
# - GitHub Actions 6時間制限対策
# - 全24場を4グループに分割
# - グループ単位で実行・保存
# - 最後にグループ結果を合算
# ============================================================

ORCHESTRATOR_GROUPS = {
    "A": ["kiryu", "toda", "edogawa", "heiwajima", "tamagawa", "hamanako"],
    "B": ["gamagori", "tokoname", "tsu", "mikuni", "biwako", "suminoe"],
    "C": ["amagasaki", "naruto", "marugame", "kojima", "miyajima", "tokuyama"],
    "D": ["shimonoseki", "wakamatsu", "ashiya", "fukuoka", "karatsu", "omura"],
}

ORCHESTRATOR_DIR_NAME = "orchestrator_results"


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def orchestrator_dir():
    return ensure_dir(os.path.join(SAVE_DIR, ORCHESTRATOR_DIR_NAME))


def parse_bool_flag(value, default=False):
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ["1", "true", "t", "yes", "y", "on"]:
        return True
    if s in ["0", "false", "f", "no", "n", "off"]:
        return False
    return default


def resolve_orchestrator_codes(group_or_codes):
    """
    group_or_codes:
      A/B/C/D/all
      or "karatsu,mikuni"
    """
    q = (group_or_codes or "").strip()
    if not q:
        return [], "UNKNOWN"

    uq = q.upper()
    if uq in ORCHESTRATOR_GROUPS:
        return ORCHESTRATOR_GROUPS[uq], uq

    if q.lower() in ["all", "全部", "ぜんぶ", "24場"]:
        codes = []
        for group_codes in ORCHESTRATOR_GROUPS.values():
            for c in group_codes:
                if c not in codes:
                    codes.append(c)
        return codes, "ALL"

    codes = parse_place_codes_input(q)
    label = "CUSTOM"
    return codes, label


def write_rows_csv(path, rows):
    """グループ合算用に行データをCSV保存する。"""
    fieldnames = get_csv_fieldnames()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def load_rows_from_many_csv(paths):
    rows = []
    for path in paths:
        rows.extend(read_rows_from_csv(path))
    return rows


def save_orchestrator_state(state):
    path = os.path.join(orchestrator_dir(), "orchestrator_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("✅ orchestrator_state保存:", path)
    return path


def run_orchestrator_group(group="A", limit=300, max_days=180, base_date="", skip_x=True, include_deep=False):
    """
    v25.4: 1グループだけ実行する。
    GitHub Actionsでは matrix で A/B/C/D を並列実行する想定。
    """
    codes, group_label = resolve_orchestrator_codes(group)
    if not codes:
        print("❌ オーケストレーター対象場がありません:", group)
        return [], {}

    out_dir = orchestrator_dir()
    started_at = datetime.now().isoformat(timespec="seconds")

    print("\n===== v25.4 オーケストレーター グループ実行 =====")
    print("group:", group_label)
    print("places:", ", ".join(PLACE_MAP_REV.get(c, c) for c in codes))
    print("limit:", limit, "max_days:", max_days, "base_date:", base_date or "today")
    print("skip_x:", skip_x, "include_deep:", include_deep)

    all_rows = []
    place_summaries = {}

    for code in codes:
        print("\n==============================")
        print("ORCH開始:", group_label, PLACE_MAP_REV.get(code, code))
        print("==============================")

        urls = get_recent_completed_race_urls(code, base_date, limit=limit, max_days=max_days, debug=True)
        prefix = f"orch_{group_label}_{code}_recent_{len(urls)}"
        rows, summary = backtest_urls(
            urls,
            skip_x=skip_x,
            include_deep=include_deep,
            filename_prefix=prefix
        )
        all_rows.extend(rows)
        place_summaries[code] = summary

        # 場ごとの途中状態も保存
        state = {
            "version": "v25.6",
            "group": group_label,
            "started_at": started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "completed_places": list(place_summaries.keys()),
            "remaining_places": [c for c in codes if c not in place_summaries],
            "limit": limit,
            "max_days": max_days,
            "base_date": base_date,
            "skip_x": skip_x,
            "include_deep": include_deep,
        }
        save_orchestrator_state(state)

    group_rows_path = os.path.join(out_dir, f"backtest_group_{group_label}_rows.csv")
    write_rows_csv(group_rows_path, all_rows)

    group_summary = build_summary(all_rows)
    group_summary["version"] = "v25.4"
    group_summary["orchestrator_group"] = group_label
    group_summary["places"] = codes
    group_summary["場別サマリ"] = place_summaries
    group_summary["rows_csv"] = group_rows_path

    group_summary_path = os.path.join(out_dir, f"backtest_group_{group_label}_summary.json")
    with open(group_summary_path, "w", encoding="utf-8") as f:
        json.dump(group_summary, f, ensure_ascii=False, indent=2)

    print("✅ グループ行CSV保存:", group_rows_path)
    print("✅ グループsummary保存:", group_summary_path)
    print(json.dumps(group_summary, ensure_ascii=False, indent=2))

    return all_rows, group_summary


def combine_orchestrator_results():
    """
    v25.4: orchestrator_results/backtest_group_*_rows.csv を合算して summary を作る。
    """
    out_dir = orchestrator_dir()
    row_files = sorted(glob.glob(os.path.join(out_dir, "backtest_group_*_rows.csv")))

    if not row_files:
        print("❌ 合算対象CSVがありません:", out_dir)
        return {}

    print("\n===== v25.4 オーケストレーター結果合算 =====")
    for f in row_files:
        print("対象:", f)

    rows = load_rows_from_many_csv(row_files)
    combined = build_summary(rows)
    combined["version"] = "v25.4"
    combined["orchestrator_combined_files"] = row_files

    combined_path = os.path.join(out_dir, "backtest_orchestrator_combined_summary.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    # ActionsのShow Summaryで見やすいようにルートにも保存
    root_path = save_path("backtest_orchestrator_combined_summary.json")
    with open(root_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    print("✅ オーケストレーター合算JSON保存:", combined_path)
    print("✅ ルートにも保存:", root_path)
    print(json.dumps(combined, ensure_ascii=False, indent=2))
    return combined


def run_orchestrator_interactive():
    print("\n===== v25.4 オーケストレーター =====")
    print("グループ:")
    for k, codes in ORCHESTRATOR_GROUPS.items():
        print(f"  {k}: " + " / ".join(PLACE_MAP_REV.get(c, c) for c in codes))
    print("  all: 全グループ")
    print("  combine: 保存済みグループ結果を合算")

    group = input("実行グループ（A/B/C/D/all/combine）: ").strip() or "A"
    if group.lower() == "combine":
        combine_orchestrator_results()
        return

    base_date = input("基準日 YYYY-MM-DD（空欄なら今日）: ").strip()
    limit_s = input("各場の検証件数（空欄なら300）: ").strip()
    max_days_s = input("最大探索日数（空欄なら180）: ").strip()
    skip_x_s = input("Xランク見送りを反映しますか？ y/n（おすすめ y）: ").strip().lower()
    deep_s = input("詳細取得しますか？ y/n（高速なら n）: ").strip().lower()

    limit = int(limit_s) if limit_s.isdigit() else 300
    max_days = int(max_days_s) if max_days_s.isdigit() else 180
    skip_x = skip_x_s != "n"
    include_deep = deep_s == "y"

    if group.lower() == "all":
        # 1回でallを選べるが、GitHub Actionsでは6時間制限に注意。
        # 本番は matrix の A/B/C/D 並列推奨。
        for g in ["A", "B", "C", "D"]:
            run_orchestrator_group(g, limit=limit, max_days=max_days, base_date=base_date, skip_x=skip_x, include_deep=include_deep)
        combine_orchestrator_results()
    else:
        run_orchestrator_group(group, limit=limit, max_days=max_days, base_date=base_date, skip_x=skip_x, include_deep=include_deep)


def run_cli():
    parser = argparse.ArgumentParser(description="ボートレース予想・検証ツール v25.4")
    parser.add_argument("--orchestrator", action="store_true", help="v25.4 オーケストレーターを実行")
    parser.add_argument("--group", default="A", help="A/B/C/D/all/combine または 場コードカンマ区切り")
    parser.add_argument("--limit", type=int, default=300, help="各場の検証件数")
    parser.add_argument("--days", type=int, default=180, help="最大探索日数")
    parser.add_argument("--base-date", default="", help="基準日 YYYY-MM-DD")
    parser.add_argument("--skip-x", default="y", help="Xランク見送り反映 y/n")
    parser.add_argument("--deep", default="n", help="詳細取得 y/n")
    args = parser.parse_args()

    if args.orchestrator:
        if str(args.group).lower() == "combine":
            combine_orchestrator_results()
        else:
            run_orchestrator_group(
                args.group,
                limit=args.limit,
                max_days=args.days,
                base_date=args.base_date,
                skip_x=parse_bool_flag(args.skip_x, True),
                include_deep=parse_bool_flag(args.deep, False),
            )
        return True

    return False


def main():
    print("\n===== ボートレース予想・検証ツール v25.6 Explainableログ版 =====")
    print("保存先:", SAVE_DIR)
    print("1: 締切前レース予想")
    print("2: URL直接指定で予想/取得テスト")
    print("5: 三国の直近Nレースをバックテスト（20件から推奨）")
    print("6: 任意場の直近Nレースをバックテスト（20件から推奨）")
    print("7: HTMLキーワード監査（v24 URL役割別 /data等を確認）")
    print("8: prediction_history分析（v25.4 / 条件抽出）")
    print("9: オーケストレーター実行（v25.4 / 分割バックテスト）")

    mode = input("\nモードを選んでください: ").strip()

    if mode == "1":
        races = get_available_races()
        if not races:
            print("締切前のレースが見つかりませんでした")
            return
        print("\n===== 選択可能レース一覧 =====")
        for i, r in enumerate(races, start=1):
            print(f"{i}. {r['place_jp']} {r['date']} {r['race_no']} / 締切 {r['deadline'] or '不明'}")
        q = input("\n取得したいレースを入力してください: ").strip()
        selected = None
        if q.isdigit():
            try:
                selected = races[int(q)-1]["url"]
            except Exception:
                selected = None
        else:
            selected = select_race_by_query(races, q)
        if not selected:
            print("指定レースなし")
            return
        data = parse_race_detail(selected, include_deep=False, debug=True)
        pred = make_machine_bets(data)
        prompt_file = save_path("race_data_v11.json")
        with open(prompt_file, "w", encoding="utf-8") as f:
            json.dump({"当該レース情報": data, "機械判定": pred}, f, ensure_ascii=False, indent=2)
        print(json.dumps(pred, ensure_ascii=False, indent=2))
        print("✅ 保存:", prompt_file)
    elif mode == "2":
        run_url_test()
    elif mode == "5":
        run_recent_backtest("mikuni")
    elif mode == "6":
        print("選択可能:", " / ".join(PLACE_MAP.keys()))
        place_jp = input("場名を入力してください（例: 若松 / 三国,住之江,唐津,徳山 / all）: ").strip()
        place_codes = parse_place_codes_input(place_jp)
        if not place_codes:
            print("❌ 有効な場が指定されていないため中止します。")
            return
        if len(place_codes) == 1:
            run_recent_backtest(place_codes[0])
        else:
            run_recent_backtest_multi(place_codes)
    elif mode == "7":
        run_keyword_audit()
    elif mode == "8":
        run_history_analysis()
    elif mode == "9":
        run_orchestrator_interactive()
    else:
        print("未対応モードです")


if __name__ == "__main__":
    if not run_cli():
        main()
