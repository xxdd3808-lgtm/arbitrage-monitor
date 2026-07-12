"""封闭基金折价年化监控

逻辑：折价买入封基/定开基金持有到期（或开放期），折价收敛为收益。
- 双条件阈值（2026-07-12 调整，折价优先）：
  - 折价率 > 5%（sealed_fund_discount_threshold）-- 核心条件，安全垫够厚
  - 年化 > 3%（sealed_fund_annualized_threshold）-- 辅助条件，比货基略高即可
- 折价是实际收益，年化只是资金效率参考（时间短年化会虚高）
- 参考：张翼轸雪球文章"折价率是安全垫的厚薄，年化是滚动操作的资金效率"

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
        # mat 是 naive datetime，today 是 aware，统一为 naive
        if today.tzinfo is not None:
            today = today.replace(tzinfo=None)
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
    discount_threshold = config.get("sealed_fund_discount_threshold", 0.03)
    annualized_threshold = config.get("sealed_fund_annualized_threshold", 0.04)

    for item in items:
        code = item["code"]
        name = item["name"]
        # 开放日优先用 config 预设，否则自动从东方财富抓取
        maturity = item.get("maturity_date", "")
        if not maturity:
            maturity = base.get_fund_open_date(code)
            if maturity:
                print(f"[封基] {name}({code}) 自动抓取开放日: {maturity}")
            else:
                print(f"[封基] {name}({code}) 开放日抓取失败，跳过")
                continue

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

        # 异常数据过滤：溢价 > 20% 视为数据错误（净值滞后/分红除权等），跳过
        # 正常封基折价率在 -10% ~ +5% 之间，超过 20% 多为数据问题
        if discount > 0.20:
            print(f"  异常溢价{discount*100:+.2f}%（疑似净值滞后/分红除权），跳过")
            continue

        annualized = calc_annualized_discount(discount, maturity)

        discount_str = f"{discount*100:+.2f}%"
        ann_str = f"{annualized*100:.1f}%" if annualized else "N/A"
        print(f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date}) 折价{discount_str} 到期{maturity} 年化{ann_str}")

        # 双条件：折价 > 3% 且 年化 > 4%
        if (annualized and annualized >= annualized_threshold
                and discount < -discount_threshold):
            key = f"{code}_sealed_discount"
            if not base.already_notified(state, key):
                alerts.append(
                    f"📊 封基 {name}({code}) 折价 {discount_str} | 年化 {ann_str}\n"
                    f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date})\n"
                    f"  到期 {maturity}\n"
                    f"  -> 买入持有到期/开放期，折价收敛为收益\n"
                    f"  ⚠️ 注意: 部分定开基金成交额低，注意流动性风险"
                )
                base.mark_notified(state, key, {"annualized": ann_str, "discount": discount_str})
                base.record_baseline(state, code, "sealed_discount", price, nav)

    return alerts
