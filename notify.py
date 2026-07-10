#!/usr/bin/env python3
"""多品种套利机会监控 - 统一推送入口

扫描所有品种的套利信号，汇总后通过 PushPlus 推送。
每个信号每天最多推送 1 次（state.json 去重）。

品种：
- QDII-LOF：折溢价 + 申购赎回状态变化（P0/P1/P2）
- 封闭基金：折价年化超阈值
- 可转债：强赎公告/最后交易日/到期保本/低溢价
"""

import sys
import os
from datetime import datetime

# 确保能 import monitors 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitors import base
from monitors import qdii_lof
from monitors import sealed_fund
from monitors import convertible_bond

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    import json
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    print(f"\n{'='*60}")
    print(f"多品种套利机会扫描 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    config = load_config()
    state = base.load_state()

    all_alerts = []

    # 1. QDII-LOF
    print("\n--- QDII-LOF ---")
    try:
        alerts = qdii_lof.check(config, state)
        all_alerts.extend(alerts)
    except Exception as e:
        print(f"[ERROR] QDII-LOF 模块异常: {e}")
        import traceback; traceback.print_exc()

    # 2. 封闭基金
    print("\n--- 封闭基金 ---")
    try:
        alerts = sealed_fund.check(config, state)
        all_alerts.extend(alerts)
    except Exception as e:
        print(f"[ERROR] 封基模块异常: {e}")

    # 3. 可转债
    print("\n--- 可转债 ---")
    try:
        alerts = convertible_bond.check(config, state)
        all_alerts.extend(alerts)
    except Exception as e:
        print(f"[ERROR] 可转债模块异常: {e}")

    # 推送
    print(f"\n{'='*60}")
    print(f"扫描完成，共 {len(all_alerts)} 条信号")
    print(f"{'='*60}")

    if all_alerts:
        title = f"🔔 套利机会提醒（{len(all_alerts)} 条）"
        body = "\n\n".join(all_alerts)
        body += f"\n\n---\n扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        print(f"\n[NOTIFY] 推送 {len(all_alerts)} 条信号...")
        base.send_pushplus(title, body)
    else:
        print("[INFO] 无触发信号，不推送")

    base.save_state(state)
    print("[DONE] state.json 已保存")


if __name__ == "__main__":
    main()
