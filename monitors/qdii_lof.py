"""QDII-LOF 折溢价监控

信号：
- P1 可操作信号：开放申购 + 持续溢价 > 5%（申购->T+2卖出）
- P2 极端信号：折价 > 8%（买入+赎回，安全垫厚）

注：P0（申购恢复检测）已移除。原 P0 依赖 config.json 手动维护 buy_status，
形成"用户改 config -> 系统通知用户"的循环通知，违背监控系统初衷。
P1 已覆盖"开放申购 + 溢价"场景，无需 P0。

数据源：
- 实时价格：Sina
- 实时估值(IOPV)：天天基金 gsz（部分LOF有），fallback 到最新净值
- 申购赎回状态：config.json 预设（仅用于 P1 判断是否开放申购）
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
                # 反馈层：记录基线用于 T+2 回看
                base.record_baseline(state, code, "qdii_p1", price, iopv)

        # P2 信号：极端折价（>8%）-- 买入+赎回套利，安全垫厚
        if premium <= -discount_threshold_p2:
            p2_key = f"{code}_qdii_p2_discount"
            if not base.already_notified(state, p2_key):
                alerts.append(
                    f"🔵 P2 {name}({code}) 极端折价 {premium_str}\n"
                    f"  价格¥{price:.4f} IOPV¥{iopv:.4f}({iopv_source})\n"
                    f"  赎回:{curr_redeem} -> 如开放赎回，买入+赎回安全垫{abs(premium)*100-1.5:.1f}%"
                )
                base.mark_notified(state, p2_key, {"premium": premium_str})
                # 反馈层：记录基线用于 T+30 回看
                base.record_baseline(state, code, "qdii_p2", price, iopv)

    return alerts
