"""封闭基金折价年化监控

逻辑：折价买入封基持有到期，折价收敛为确定性收益。
- 折价年化 = 折价率 / 剩余年限
- 年化 > 8% 值得关注，年化 > 12% 重点关注

数据源：
- 实时价格：Sina
- 最新净值：akshare
- 到期日：config.json 预设（封基到期日固定，不需实时拉取）
"""

from datetime import datetime
from . import base


def calc_annualized_discount(discount_rate, maturity_date, today=None):
    """计算折价年化。discount_rate 为负表示折价（如 -0.08 = 折价8%）"""
    if not maturity_date or discount_rate is None:
        return None
    if today is None:
        today = datetime.now()
    try:
        mat = datetime.strptime(str(maturity_date)[:10], "%Y-%m-%d")
        days = (mat - today).days
        if days <= 0:
            return None  # 已到期
        years = days / 365.0
        if years <= 0:
            return None
        # 折价率是负数，年化收益 = |折价率| / 年限
        return abs(discount_rate) / years
    except (ValueError, TypeError):
        return None


def check(config, state):
    alerts = []
    items = config.get("sealed_fund", [])
    threshold = config.get("sealed_fund_threshold", 0.08)  # 默认年化8%

    for item in items:
        code = item["code"]
        name = item["name"]
        maturity = item.get("maturity_date", "")

        print(f"[封基] {name}({code}) ...")

        price_data = base.get_realtime_price_sina(code)
        nav, nav_date = base.get_fund_latest_nav(code)

        if not price_data or not nav:
            print(f"  数据获取失败，跳过")
            continue

        price = price_data["last"]
        discount = base.calc_premium(price, nav)  # 负数=折价
        if discount is None:
            continue

        annualized = calc_annualized_discount(discount, maturity)

        discount_str = f"{discount*100:+.2f}%"
        ann_str = f"{annualized*100:.1f}%" if annualized else "N/A"
        print(f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date}) 折价{discount_str} 到期{maturity} 年化{ann_str}")

        if annualized and annualized >= threshold and discount < 0:
            key = f"{code}_sealed_discount"
            if not base.already_notified(state, key):
                alerts.append(
                    f"📊 封基 {name}({code}) 折价年化 {ann_str}\n"
                    f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date})\n"
                    f"  折价 {discount_str} | 到期 {maturity}\n"
                    f"  -> 买入持有到期，折价收敛是合同保证的确定性收益\n"
                    f"  ⚠️ 注意: 部分封基成交额低（日<百万），注意流动性风险，建议分散持有"
                )
                base.mark_notified(state, key, {"annualized": ann_str})

    return alerts
