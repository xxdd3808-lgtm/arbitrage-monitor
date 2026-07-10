"""可转债多信号监控

数据源：akshare bond_cb_redeem_jsl（集思录强赎数据，含320只转债）
  含：现价、正股价、转股价、最后交易日、到期日、强赎状态

信号：
- P1 强赎新公告：强赎状态变为"已公告强赎"（state对比检测变化）
- P1 强赎最后交易日临近：已公告强赎 且 最后交易日<=30天
- P2 到期保本：现价<105 且 到期日<1年
- P2 低溢价机会：溢价率<5% 且 现价<115
"""

from datetime import datetime
from . import base


def calc_conversion_premium(bond_price, stock_price, conversion_price):
    """转股溢价率 = (债现价 - 转股价值) / 转股价值
    转股价值 = 正股价/转股价 * 100"""
    try:
        if not all([bond_price, stock_price, conversion_price]) or conversion_price <= 0:
            return None
        conv_value = stock_price / conversion_price * 100
        if conv_value <= 0:
            return None
        return bond_price / conv_value - 1
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def days_to(date_str):
    """距今天数"""
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return (d - datetime.now()).days
    except (ValueError, TypeError):
        return None


def check(config, state):
    alerts = []
    cb_config = config.get("convertible_bond", {})
    redeem_alert_days = cb_config.get("redeem_alert_days", 30)       # 强赎最后交易日提前几天提醒
    low_premium_threshold = cb_config.get("low_premium", 0.05)        # 低溢价阈值
    low_premium_price_max = cb_config.get("low_premium_price_max", 115)  # 低溢价转债价格上限
    maturity_price_max = cb_config.get("maturity_price_max", 105)      # 到期保本价格上限
    maturity_days_max = cb_config.get("maturity_days", 365)            # 到期保本剩余天数上限

    print(f"[可转债] 拉取集思录强赎数据...")
    try:
        import akshare as ak
        df = ak.bond_cb_redeem_jsl()
    except Exception as e:
        print(f"[可转债] 数据获取失败: {e}")
        return alerts

    if df is None or len(df) == 0:
        print("[可转债] 无数据")
        return alerts

    print(f"[可转债] 共 {len(df)} 只转债")

    new_redeem = []
    redeem_urgent = []
    maturity_opps = []
    low_premium_opps = []

    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        name = str(row.get("名称", "")).strip()
        price = row.get("现价")
        stock_price = row.get("正股价")
        conv_price = row.get("转股价")
        redeem_status = str(row.get("强赎状态", "")).strip()
        last_trade_date = row.get("最后交易日")
        maturity_date = row.get("到期日")

        if not code or price is None:
            continue

        try:
            price = float(price)
        except (TypeError, ValueError):
            continue

        # 信号1: 强赎新公告（state对比）
        if "已公告" in redeem_status:
            redeem_key = f"{code}_cb_redeemed"
            if not state.get(redeem_key, {}).get("notified"):
                new_redeem.append(f"  {name}({code}) 现价{price:.2f} | 强赎状态: {redeem_status}")
                state[redeem_key] = {"notified": True, "date": base.TODAY, "status": redeem_status}
            # 已记录过的，更新状态
            elif state.get(redeem_key, {}).get("status") != redeem_status:
                state[redeem_key]["status"] = redeem_status

            # 信号2: 强赎最后交易日临近
            days = days_to(last_trade_date)
            if days is not None and 0 < days <= redeem_alert_days:
                urgent_key = f"{code}_cb_redeem_urgent"
                if not base.already_notified(state, urgent_key):
                    redeem_urgent.append(
                        f"  {name}({code}) 现价{price:.2f} | 最后交易日{last_trade_date}（剩{days}天）"
                    )
                    base.mark_notified(state, urgent_key, {"days": days})

        # 信号3: 到期保本（低价+临近到期）
        mat_days = days_to(maturity_date)
        if (mat_days is not None and 0 < mat_days <= maturity_days_max
                and price <= maturity_price_max
                and not code.startswith("4")):  # 排除退市转债
            mat_key = f"{code}_cb_maturity"
            if not base.already_notified(state, mat_key):
                maturity_opps.append(
                    f"  {name}({code}) 现价{price:.2f} | 到期{maturity_date}（剩{mat_days}天）"
                )
                base.mark_notified(state, mat_key, {"days": mat_days})

        # 信号4: 低溢价+低价
        premium = calc_conversion_premium(price, stock_price, conv_price)
        # 过滤异常：退市转债(代码4开头)、转股价值过低(正股暴跌)、极端负溢价(数据异常)
        conv_value = None
        try:
            if stock_price and conv_price and conv_price > 0:
                conv_value = float(stock_price) / float(conv_price) * 100
        except (TypeError, ValueError):
            pass
        is_valid = (
            premium is not None
            and premium <= low_premium_threshold
            and premium >= -0.10  # 排除极端负溢价（数据异常）
            and price <= low_premium_price_max
            and "已公告" not in redeem_status
            and not code.startswith("4")  # 排除退市转债
            and conv_value is not None and conv_value >= 50  # 排除正股暴跌的废债
        )
        if is_valid:
            lp_key = f"{code}_cb_low_premium"
            if not base.already_notified(state, lp_key):
                low_premium_opps.append(
                    f"  {name}({code}) 现价{price:.2f} 溢价{premium*100:.1f}% | 正股{stock_price} 转股价{conv_price} 转股价值{conv_value:.1f}"
                )
                base.mark_notified(state, lp_key, {"premium": f"{premium*100:.1f}%"})

    if new_redeem:
        alerts.append("📢【可转债-新强赎公告】\n" + "\n".join(new_redeem[:10]))
    if redeem_urgent:
        alerts.append(f"⚠️【可转债-强赎最后交易日临近（<{redeem_alert_days}天）】\n" + "\n".join(redeem_urgent[:10]))
    if maturity_opps:
        alerts.append(f"💰【可转债-到期保本（价格<{maturity_price_max} 且 剩余<{maturity_days_max}天）】\n" + "\n".join(maturity_opps[:10]))
    if low_premium_opps:
        alerts.append(f"📈【可转债-低溢价机会（溢价<{low_premium_threshold*100:.0f}% 且 价格<{low_premium_price_max}）】\n" + "\n".join(low_premium_opps[:10]))

    return alerts
