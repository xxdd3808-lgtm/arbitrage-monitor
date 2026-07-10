"""要约收购套利监控

只推"控股股东增持型"要约（安全垫有效），排除"实控人变更型"（溢价高安全垫失效）。
数据源：config.json 手动维护要约信息 + Sina 实时价格。

调研验证（知乎案例+集思录实战）：
- 赚钱：控股股东增持型，公告后10%以内买入，等脉冲行情
- 亏钱：海螺水泥要约西部水泥（港股要约失败）、南国置业（部分要约按比例收购）

信号：控股股东增持型 + 当前溢价 < 10%
不推：实控人变更型（溢价太高，安全垫作用没有）
"""

from datetime import datetime
from . import base


def check(config, state):
    alerts = []
    items = config.get("tender_offer", [])
    max_premium = config.get("tender_offer_max_premium", 0.10)  # 溢价上限10%

    for item in items:
        code = str(item["code"])
        name = item["name"]
        tender_price = item.get("tender_price")
        tender_type = item.get("tender_type", "")
        end_date = item.get("end_date", "")
        is_partial = item.get("is_partial", False)  # 部分要约

        if not tender_price:
            continue

        print(f"[要约] {name}({code}) ...")

        # 排除实控人变更型
        if "实控人变更" in tender_type or "实际控制人变更" in tender_type:
            print(f"  跳过：实控人变更型，安全垫失效")
            continue

        # 获取实时价格
        price_data = base.get_realtime_price_sina(code)
        if not price_data:
            print(f"  价格获取失败，跳过")
            continue

        current_price = price_data["last"]
        premium = current_price / tender_price - 1
        premium_str = f"{premium*100:+.2f}%"

        # 距截止日天数
        days_left = None
        if end_date:
            try:
                end = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
                days_left = (end - datetime.now()).days
            except (ValueError, TypeError):
                pass

        print(f"  现价¥{current_price:.2f} 要约价¥{tender_price:.2f} 溢价{premium_str} | {tender_type} | 剩{days_left}天")

        # 信号：溢价 < 10% 且未过期
        if premium < max_premium and (days_left is None or days_left > 0):
            key = f"{code}_tender_offer"
            if not base.already_notified(state, key):
                risk_note = ""
                if is_partial:
                    risk_note = "\n  ⚠️ 部分要约：申购量>收购量时按比例收购，余股按市价卖"
                if days_left is not None and days_left <= 7:
                    risk_note += f"\n  ⏰ 仅剩{days_left}天，注意截止时间"

                alerts.append(
                    f"📋 要约收购 {name}({code})\n"
                    f"  现价¥{current_price:.2f} 要约价¥{tender_price:.2f} 溢价{premium_str}\n"
                    f"  类型: {tender_type} | 截止: {end_date}（剩{days_left}天）\n"
                    f"  -> 买入并接受要约，预计收益{abs(premium)*100:.1f}%{risk_note}"
                )
                base.mark_notified(state, key, {"premium": premium_str})

    return alerts
