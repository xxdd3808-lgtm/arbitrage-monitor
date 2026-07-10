"""QDII-LOF 折溢价监控

信号：
- P0 黄金信号：申购状态 暂停->开放 + 当前溢价 > 3%（套利窗口打开）
- P1 可操作信号：开放申购 + 持续溢价 > 5%
- P2 极端信号：折价 > 8%（罕见但安全垫厚）/ 溢价 > 15%（极端卖出信号）

数据源：
- 实时价格：Sina
- 实时估值(IOPV)：天天基金 gsz（部分LOF有），fallback 到最新净值
- 申购赎回状态：config.json 预设
"""

from . import base


def check(config, state):
    """检查所有 QDII-LOF，返回 alerts 列表"""
    alerts = []
    items = config.get("qdii_lof", [])

    for item in items:
        code = item["code"]
        name = item["name"]
        curr_buy = item.get("buy_status", "未知")
        curr_redeem = item.get("redeem_status", "未知")
        premium_threshold_p1 = item.get("premium_threshold", 0.05)
        discount_threshold_p2 = item.get("discount_threshold", 0.08)

        print(f"[QDII] {name}({code}) ...")

        # 获取数据
        price_data = base.get_realtime_price_sina(code)
        valuation = base.get_fund_realtime_valuation(code)

        if not price_data:
            print(f"  实时价格获取失败，跳过")
            continue

        price = price_data["last"]

        # IOPV 估算：优先用天天基金 gsz，fallback 到最新净值
        iopv = None
        iopv_source = ""
        nav_date = ""
        if valuation and valuation.get("gsz"):
            iopv = valuation["gsz"]
            iopv_source = f"实时估值({valuation['gztime']})"
            nav_date = valuation.get("nav_date", "")
        else:
            nav, nav_date = base.get_fund_latest_nav(code)
            if nav:
                iopv = nav
                iopv_source = f"最新净值({nav_date})"

        if not iopv:
            print(f"  IOPV 估算失败，跳过")
            continue

        premium = base.calc_premium(price, iopv)
        if premium is None:
            continue

        premium_str = f"{premium*100:+.2f}%"
        print(f"  价格¥{price:.4f} IOPV¥{iopv:.4f}({iopv_source}) 折溢价{premium_str} | 申购:{curr_buy} 赎回:{curr_redeem}")

        # P0 信号：申购状态变化（暂停->开放）
        prev_buy_key = f"{code}_qdii_buy_status"
        prev_buy = state.get(prev_buy_key, {}).get("status", "")
        if prev_buy and prev_buy != curr_buy:
            if "开放" in curr_buy and "暂停" in prev_buy:
                # 申购恢复！
                p0_key = f"{code}_qdii_p0_buyopen"
                if not base.already_notified(state, p0_key):
                    extra = ""
                    if premium > 0:
                        extra = f"\n  当前溢价 {premium_str} -> 可申购+卖出套利，预计收益 {premium*100:.1f}%"
                    elif premium < -0.02:
                        extra = f"\n  当前折价 {premium_str} -> 申购无套利价值"
                    else:
                        extra = f"\n  当前折溢价 {premium_str} -> 中性"
                    alerts.append(
                        f"🟢 P0 {name}({code}) 申购恢复！\n"
                        f"  {prev_buy} -> {curr_buy}\n"
                        f"  价格¥{price:.4f} IOPV¥{iopv:.4f}{extra}"
                    )
                    base.mark_notified(state, p0_key, {"from": prev_buy, "to": curr_buy})
            elif "暂停" in curr_buy and "开放" in prev_buy:
                p0_key = f"{code}_qdii_p0_buyclose"
                if not base.already_notified(state, p0_key):
                    alerts.append(
                        f"⚠️ P0 {name}({code}) 申购暂停\n"
                        f"  {prev_buy} -> {curr_buy}\n"
                        f"  溢价套利窗口关闭"
                    )
                    base.mark_notified(state, p0_key, {"from": prev_buy, "to": curr_buy})

        # 更新状态
        if curr_buy != "未知":
            state[prev_buy_key] = {"status": curr_buy, "date": base.TODAY}

        # P1 信号：开放申购 + 持续溢价 > 阈值
        if "开放" in curr_buy and premium >= premium_threshold_p1:
            p1_key = f"{code}_qdii_p1_premium"
            if not base.already_notified(state, p1_key):
                alerts.append(
                    f"📢 P1 {name}({code}) 开放申购 + 溢价 {premium_str}\n"
                    f"  价格¥{price:.4f} IOPV¥{iopv:.4f}({iopv_source})\n"
                    f"  -> 申购->T+2卖出，预计收益 {(premium-0.0015)*100:.1f}%（扣申购费0.15%）"
                )
                base.mark_notified(state, p1_key, {"premium": premium_str})

        # P2 信号：极端折价（>8%）或极端溢价（>15%）
        if premium <= -discount_threshold_p2:
            p2_key = f"{code}_qdii_p2_discount"
            if not base.already_notified(state, p2_key):
                alerts.append(
                    f"🔵 P2 {name}({code}) 极端折价 {premium_str}\n"
                    f"  价格¥{price:.4f} IOPV¥{iopv:.4f}({iopv_source})\n"
                    f"  赎回:{curr_redeem} -> 如开放赎回，买入+赎回有安全垫"
                )
                base.mark_notified(state, p2_key, {"premium": premium_str})
        elif premium >= 0.15:
            p2_key = f"{code}_qdii_p2_premium_extreme"
            if not base.already_notified(state, p2_key):
                alerts.append(
                    f"🔴 P2 {name}({code}) 极端溢价 {premium_str}\n"
                    f"  价格¥{price:.4f} IOPV¥{iopv:.4f}({iopv_source})\n"
                    f"  -> 已持有者极端卖出信号"
                )
                base.mark_notified(state, p2_key, {"premium": premium_str})

    return alerts
