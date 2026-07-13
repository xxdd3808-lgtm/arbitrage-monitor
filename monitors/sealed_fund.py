"""封闭基金折价年化监控

⚠️ 2026-07-13 暂停推送（保留监控+看板）
原因：经豆包AI核实报告，封基折价套利不是确定性套利：
1. 净值波动 >> 安全垫：偏股基金单日波动3-5%，安全垫仅1-4%
2. 赎回费侵蚀：0.6-0.75%赎回费占折价13-35%
3. 流动性差：日均100-300万，大资金无法参与
4. 资金抢筹：折价已被市场快速抹平
本质是"带薄安全垫的股票投资"，违背项目"确定性套利"定位。

config.json 的 sealed_fund_push_enabled 控制是否推送：
- false（默认）：只扫描+打印数据（看板可见），不推送
- true：恢复推送（未来市场极端折价或找到确定性方案时开启）

推送逻辑（开启时）：折价>4% 且 年化>10%，首次推 + 月底提醒。

数据源：
- 实时价格：Sina
- 最新净值：akshare
- 开放日：自动从东方财富 F10 抓取（base.get_fund_open_date）
"""

from datetime import datetime
from . import base


def calc_annualized_discount(discount_rate, maturity_date, today=None):
    """计算折价年化。discount_rate 为负表示折价（如 -0.08 = 折价8%）"""
    if not maturity_date or discount_rate is None:
        return None
    if today is None:
        today = base.now_beijing()
    try:
        mat = datetime.strptime(str(maturity_date)[:10], "%Y-%m-%d")
        if today.tzinfo is not None:
            today = today.replace(tzinfo=None)
        days = (mat - today).days
        if days <= 0:
            return None
        years = days / 365.0
        if years <= 0:
            return None
        return abs(discount_rate) / years
    except (ValueError, TypeError):
        return None


def check(config, state):
    alerts = []
    items = config.get("sealed_fund", [])
    discount_threshold = config.get("sealed_fund_discount_threshold", 0.04)
    annualized_threshold = config.get("sealed_fund_annualized_threshold", 0.10)
    push_enabled = config.get("sealed_fund_push_enabled", False)

    if not push_enabled:
        print("[封基] 推送已暂停（sealed_fund_push_enabled=false），仅监控不推送")
        print("  原因：封基折价非确定性套利，净值波动>>安全垫。详见 CLAUDE.md")

    now = base.now_beijing()
    current_month = now.strftime("%Y-%m")
    today_day = now.day

    current_satisfied = set()
    data_failed = set()

    for item in items:
        code = item["code"]
        name = item["name"]
        key = f"{code}_sealed_discount"

        maturity = item.get("maturity_date", "")
        if not maturity:
            maturity = base.get_fund_open_date(code)
            if maturity:
                print(f"[封基] {name}({code}) 自动抓取开放日: {maturity}")
            else:
                print(f"[封基] {name}({code}) 开放日抓取失败，跳过")
                data_failed.add(key)
                continue

        print(f"[封基] {name}({code}) ...")

        price_data = base.get_realtime_price_sina(code)
        nav, nav_date = base.get_fund_latest_nav(code)

        if not price_data or not nav:
            print(f"  数据获取失败，跳过（保持现有状态）")
            data_failed.add(key)
            continue

        price = price_data["last"]
        discount = base.calc_premium(price, nav)
        if discount is None:
            data_failed.add(key)
            continue

        if discount > 0.20:
            print(f"  异常溢价{discount*100:+.2f}%（疑似净值滞后/分红除权），跳过")
            data_failed.add(key)
            continue

        annualized = calc_annualized_discount(discount, maturity)

        discount_str = f"{discount*100:+.2f}%"
        ann_str = f"{annualized*100:.1f}%" if annualized else "N/A"
        print(f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date}) 折价{discount_str} 到期{maturity} 年化{ann_str}")

        if not push_enabled:
            continue

        if (annualized and annualized >= annualized_threshold
                and discount < -discount_threshold):
            current_satisfied.add(key)

            if key not in state:
                alerts.append(
                    f"📊 封基 {name}({code}) 折价 {discount_str} | 年化 {ann_str}\n"
                    f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date})\n"
                    f"  到期 {maturity}\n"
                    f"  -> 买入持有到期/开放期，折价收敛为收益\n"
                    f"  ⚠️ 注意: 部分定开基金成交额低，注意流动性风险"
                )
                state[key] = {
                    "first_pushed": base.TODAY,
                    "last_pushed_month": current_month,
                    "discount": discount_str,
                    "annualized": ann_str,
                }
                base.record_baseline(state, code, "sealed_discount", price, nav)
                print(f"  [推送] 首次出现")
            else:
                prev = state[key]
                last_pushed_month = prev.get("last_pushed_month", "")
                if current_month != last_pushed_month and today_day >= 28:
                    alerts.append(
                        f"📊 封基 {name}({code}) 折价 {discount_str} | 年化 {ann_str}（月度提醒）\n"
                        f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date})\n"
                        f"  到期 {maturity}\n"
                        f"  首次推送: {prev.get('first_pushed', 'N/A')}\n"
                        f"  -> 仍满足阈值，机会有效"
                    )
                    state[key] = {
                        **prev,
                        "last_pushed_month": current_month,
                        "discount": discount_str,
                        "annualized": ann_str,
                        "last_remind_date": base.TODAY,
                    }
                    base.record_baseline(state, code, "sealed_discount", price, nav)
                    print(f"  [推送] 月底提醒（已重新验证仍满足阈值）")
                else:
                    print(f"  [跳过] 已推过，非月底或本月已推月度提醒")

    if push_enabled:
        keys_to_remove = [
            k for k in list(state.keys())
            if k.endswith('_sealed_discount')
            and k not in current_satisfied
            and k not in data_failed
        ]
        for k in keys_to_remove:
            print(f"  [清理] {k} 信号已消失，清除记录")
            del state[k]

    return alerts
