"""反馈层：推送信号的事后回看

记录每次推送的基线价格，T+N 后自动回看实际收益，用于校准阈值。

回看周期：
- QDII P1（申购->T+2卖出）：T+2 回看
- QDII P2（买入+赎回）：T+7 回看（赎回到账周期）
- CB 到期保本：T+30 回看（持有期间价格波动）
- 封基折价：T+30 回看

数据存储：feedback.json
- pending: 待回看的信号列表
- completed: 已回看的信号列表（含实际收益）
"""

import os
import json
from datetime import datetime, timedelta

from . import base

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_FILE = os.path.join(SCRIPT_DIR, "feedback.json")

# 信号 -> 回看天数
REVIEW_DAYS = {
    "qdii_p1": 2,
    "qdii_p2": 7,
    "cb_maturity": 30,
    "sealed_discount": 30,
}


def _load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"pending": [], "completed": []}


def _save_feedback(data):
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def review_past_signals(state):
    """回看到期的历史信号，记录实际收益到 feedback.json。

    state 里 _baselines 字段存储待回看的基线：
    [{code, signal_type, baseline_price, baseline_date, ...}, ...]
    """
    feedback = _load_feedback()
    today = base.now_beijing().strftime("%Y-%m-%d")
    today_dt = base.now_beijing()

    pending = feedback.get("pending", [])
    still_pending = []
    newly_completed = []

    for item in pending:
        signal_type = item.get("signal_type", "")
        baseline_date = item.get("baseline_date", "")
        review_days = REVIEW_DAYS.get(signal_type, 30)

        try:
            base_dt = datetime.strptime(baseline_date[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            still_pending.append(item)
            continue

        # today_dt 是 aware（北京时间），base_dt 是 naive，统一为 naive
        today_naive = today_dt.replace(tzinfo=None) if today_dt.tzinfo else today_dt
        if (today_naive - base_dt).days < review_days:
            # 还没到回看日
            still_pending.append(item)
            continue

        # 到了回看日，拉当前价格
        code = item["code"]
        price_data = base.get_realtime_price_sina(code)
        if not price_data:
            # 价格拉取失败，再等一天
            still_pending.append(item)
            continue

        current_price = price_data["last"]
        baseline_price = item["baseline_price"]
        if baseline_price > 0:
            actual_return = (current_price - baseline_price) / baseline_price
        else:
            actual_return = None

        item["review_date"] = today
        item["review_price"] = current_price
        item["actual_return"] = round(actual_return * 100, 2) if actual_return is not None else None
        newly_completed.append(item)
        print(f"  [反馈] {code} {signal_type} 基线¥{baseline_price:.4f} -> 回看¥{current_price:.4f} | 收益{item['actual_return']}%")

    feedback["pending"] = still_pending
    feedback["completed"] = feedback.get("completed", []) + newly_completed

    # 清理 completed 列表：保留最近 100 条，避免长期运行后文件膨胀
    completed = feedback.get("completed", [])
    if len(completed) > 100:
        feedback["completed"] = completed[-100:]
        print(f"  [反馈] completed 清理：{len(completed)} -> {len(feedback['completed'])} 条")

    _save_feedback(feedback)

    if newly_completed:
        print(f"  [反馈] 本轮回看完成 {len(newly_completed)} 条，累计 {len(feedback['completed'])} 条")
    else:
        print(f"  [反馈] 待回看 {len(still_pending)} 条，累计已完成 {len(feedback.get('completed', []))} 条")
