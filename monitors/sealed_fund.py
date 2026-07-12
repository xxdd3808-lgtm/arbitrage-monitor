"""封闭基金折价年化监控

逻辑：折价买入封基/定开基金持有到期（或开放期），折价收敛为收益。
- 双条件阈值（2026-07-12 定稿，资金机会成本视角）：
  - 折价率 > 3%（sealed_fund_discount_threshold）-- 绝对收益有意义（扣成本0.5%净赚2.5%+）
  - 年化 > 10%（sealed_fund_annualized_threshold）-- 满足资金机会成本（股票预期10-15%）
- 年化 = 折价 / 到期年限，年化10%+必然到期近（<1年）、折价薄（3-5%）
- 调研依据：张翼轸《封基每年白送6%》+ 零城逆影《场内折价封基指南》

推送规则（首次出现才推，不重复）：
- 首次满足阈值 -> 推 + 记录
- 持续满足 -> 不推（避免重复骚扰）
- 信号消失（折价收敛/到期）-> 清除记录
- 数据获取失败 -> 保持现状（不误清除）
- 再次满足 -> 重新推（新机会）

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
    annualized_threshold = config.get("sealed_fund_annualized_threshold", 0.10)

    # 本次扫描中"数据成功获取且满足阈值"的 key 集合
    current_satisfied = set()
    # 数据获取失败的 key（保持现状，不清除）
    data_failed = set()

    for item in items:
        code = item["code"]
        name = item["name"]
        key = f"{code}_sealed_discount"

        # 开放日优先用 config 预设，否则自动从东方财富抓取
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
        discount = base.calc_premium(price, nav)  # 负数=折价
        if discount is None:
            data_failed.add(key)
            continue

        # 异常数据过滤：溢价 > 20% 视为净值滞后/分红除权
        if discount > 0.20:
            print(f"  异常溢价{discount*100:+.2f}%（疑似净值滞后/分红除权），跳过")
            data_failed.add(key)
            continue

        annualized = calc_annualized_discount(discount, maturity)

        discount_str = f"{discount*100:+.2f}%"
        ann_str = f"{annualized*100:.1f}%" if annualized else "N/A"
        print(f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date}) 折价{discount_str} 到期{maturity} 年化{ann_str}")

        # 双条件：折价 > 3% 且 年化 > 10%
        if (annualized and annualized >= annualized_threshold
                and discount < -discount_threshold):
            current_satisfied.add(key)

            # 首次出现才推（state 里没记录）
            if key not in state:
                alerts.append(
                    f"📊 封基 {name}({code}) 折价 {discount_str} | 年化 {ann_str}\n"
                    f"  价格¥{price:.4f} 净值¥{nav:.4f}({nav_date})\n"
                    f"  到期 {maturity}\n"
                    f"  -> 买入持有到期/开放期，折价收敛为收益\n"
                    f"  ⚠️ 注意: 部分定开基金成交额低，注意流动性风险"
                )
                base.mark_notified(state, key, {"annualized": ann_str, "discount": discount_str})
                base.record_baseline(state, code, "sealed_discount", price, nav)
                print(f"  [推送] 首次出现，推送")
            else:
                print(f"  [跳过] 已推过，不重复")

    # 清理消失的信号（之前满足但现在不满足的，且不是数据失败的）
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
