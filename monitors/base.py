"""公共工具：数据获取 + PushPlus 推送 + 状态管理

数据源策略：
- 实时价格：Sina hq.sinajs.cn（最稳，不依赖 yfinance）
- 实时估值：天天基金 fundgz.1234567.com.cn（部分LOF有，QDII期货类无）
- 净值历史：akshare fund_open_fund_info_em
- 申购赎回状态：config.json 预设 + 公告检测（待完善）
"""

import os
import json
import re
import time
import requests
from datetime import datetime

import akshare as ak

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------- 状态管理 ----------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def already_notified(state, key):
    """今天是否已推送过该信号"""
    return state.get(key, {}).get("date") == TODAY


def mark_notified(state, key, extra=None):
    state[key] = {"date": TODAY, **(extra or {})}


# ---------- 数据获取 ----------
def get_realtime_price_sina(code):
    """Sina 实时行情。code=6位数字。返回 dict 或 None"""
    try:
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        r = requests.get(f"http://hq.sinajs.cn/list={prefix}{code}", timeout=10,
                         headers={"Referer": "https://finance.sina.com.cn"})
        m = re.search(r'"(.*)"', r.text)
        if m and m.group(1):
            parts = m.group(1).split(",")
            if len(parts) > 5:
                return {
                    "name": parts[0],
                    "open": float(parts[1]),
                    "prev_close": float(parts[2]),
                    "last": float(parts[3]),
                    "high": float(parts[4]),
                    "low": float(parts[5]),
                }
    except Exception:
        pass
    return None


def get_fund_realtime_valuation(code):
    """天天基金实时估值。返回 dict(name, nav_date, nav, gsz, gszzl, gztime) 或 None。
    部分LOF（QDII期货类）无此数据。"""
    try:
        r = requests.get(f"http://fundgz.1234567.com.cn/js/{code}.js", timeout=10)
        m = re.search(r'\((\{.*\})\)', r.text)
        if m:
            d = json.loads(m.group(1))
            return {
                "name": d.get("name", ""),
                "nav_date": d.get("jzrq", ""),
                "nav": float(d.get("dwjz", 0)),
                "gsz": float(d.get("gsz", 0)),
                "gszzl": float(d.get("gszzl", 0)),
                "gztime": d.get("gztime", ""),
            }
    except Exception:
        pass
    return None


def get_fund_nav_history(code):
    """akshare 净值历史。返回 DataFrame 或 None"""
    try:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        return df
    except Exception:
        return None


def get_fund_latest_nav(code):
    """最新净值 + 日期"""
    df = get_fund_nav_history(code)
    if df is None or len(df) == 0:
        return None, None
    latest = df.iloc[-1]
    return float(latest["单位净值"]), str(latest["净值日期"])[:10]


def get_fund_announcements(code, limit=20):
    """基金公告列表（akshare，仅含部分类型公告）"""
    try:
        df = ak.fund_announcement_personnel_em(symbol=code)
        if df is not None and len(df) > 0:
            return df.head(limit).to_dict("records")
    except Exception:
        pass
    return []


# ---------- 推送 ----------
def send_pushplus(title, content):
    """PushPlus 微信推送（HTTPS + 1 次重试）"""
    if not PUSHPLUS_TOKEN:
        print(f"[WARN] 无 PUSHPLUS_TOKEN，跳过推送")
        print(f"--- {title} ---\n{content}\n---")
        return False

    url = "https://www.pushplus.plus/send"
    payload = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "txt"}

    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()
            ok = data.get("code") == 200
            print(f"[PUSH] {title} -> {'OK' if ok else data}")
            if ok or data.get("code") in (903, 904):
                return ok
        except Exception as e:
            print(f"[WARN] 推送第 {attempt+1} 次失败: {e}")
            if attempt == 0:
                time.sleep(2)
    return False


# ---------- 折溢价计算 ----------
def calc_premium(price, nav):
    """折溢价率 = price/nav - 1"""
    if price and nav and nav > 0:
        return price / nav - 1
    return None
