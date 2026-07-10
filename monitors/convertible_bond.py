"""可转债确定性套利监控（只推买入能赚钱的信号）

信号（只保留确定性套利，删掉持有者防亏/投机信号）：
1. 折价转股套利：转股溢价率 < -1%
   操作：买入转债 -> 当日转股 -> 次日卖出股票
   收益：|折价率| - 卖出佣金 - 隔夜风险
   研报验证：BigQuant研报显示转股期内溢价<-0.5%胜率100%（融券对冲），散户买入转股方式有隔夜风险

2. 到期保本套利：YTM > 5% 且 到期 < 1年
   操作：买入持有到期
   收益：(到期赎回价 - 现价) / 现价，年化
   确定性：到期还本是法律义务，AA+以上违约概率<0.1%

数据源：
- bond_cb_redeem_jsl (320只，全面扫描)
- bond_cb_jsl (30只，精确YTM，集思录筛选的值得关注转债)
"""

from datetime import datetime
from . import base

# 到期赎回价估算值（多数转债在105-110之间，用106作保守估算）
ESTIMATED_REDEEM_PRICE = 106.0


def calc_conversion_premium(bond_price, stock_price, conversion_price):
    """转股溢价率 = 债现价/转股价值 - 1。负数=折价（套利机会）"""
    try:
        if not all([bond_price, stock_price, conversion_price]) or conversion_price <= 0:
            return None
        conv_value = float(stock_price) / float(conversion_price) * 100
        if conv_value <= 0:
            return None
        return bond_price / conv_value - 1
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def calc_conversion_value(stock_price, conversion_price):
    """转股价值 = 正股价/转股价*100"""
    try:
        if stock_price and conversion_price and conversion_price > 0:
            return float(stock_price) / float(conversion_price) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def days_to(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return (d - datetime.now()).days
    except (ValueError, TypeError):
        return None


def calc_ytm(price, maturity_date, exact_ytm=None):
    """计算到期收益率(YTM)。
    优先用集思录精确值，否则用(106-现价)/现价/剩余年限估算。
    返回小数（0.05 = 5%）"""
    if exact_ytm is not None and -0.5 < exact_ytm < 0.5:  # 过滤异常值
        return exact_ytm
    days = days_to(maturity_date)
    if not days or days <= 0 or price <= 0:
        return None
    years = days / 365.0
    return (ESTIMATED_REDEEM_PRICE - price) / price / years


def check(config, state):
    alerts = []
    cb_config = config.get("convertible_bond", {})
    ytm_threshold = cb_config.get("ytm_threshold", 0.05)
    discount_premium_threshold = cb_config.get("discount_premium", -0.01)
    maturity_days_max = cb_config.get("maturity_days", 365)

    print(f"[可转债] 拉取数据...")
    import akshare as ak

    # 数据源1: bond_cb_jsl (30只，有精确YTM)
    jsl_ytm_map = {}
    try:
        jsl_df = ak.bond_cb_jsl()
        for _, row in jsl_df.iterrows():
            code = str(row.get("代码", "")).strip()
            ytm_raw = row.get("到期税前收益")
            try:
                ytm = float(ytm_raw) / 100 if ytm_raw is not None else None
                if code and ytm is not None:
                    jsl_ytm_map[code] = ytm
            except (TypeError, ValueError):
                pass
        print(f"  bond_cb_jsl: {len(jsl_ytm_map)} 只精确YTM")
    except Exception as e:
        print(f"  bond_cb_jsl 失败(非致命): {e}")

    # 数据源2: bond_cb_redeem_jsl (320只，全面)
    try:
        df = ak.bond_cb_redeem_jsl()
    except Exception as e:
        print(f"[可转债] 主数据获取失败: {e}")
        return alerts

    if df is None or len(df) == 0:
        return alerts

    print(f"  bond_cb_redeem_jsl: {len(df)} 只转债")

    discount_opps = []
    maturity_opps = []

    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        name = str(row.get("名称", "")).strip()
        price = row.get("现价")
        stock_price = row.get("正股价")
        conv_price = row.get("转股价")
        maturity_date = row.get("到期日")

        if not code or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue

        # 排除退市转债
        if code.startswith("4"):
            continue

        conv_value = calc_conversion_value(stock_price, conv_price)

        # 信号1: 折价转股套利（溢价 < -1%，但排除极端负溢价<-10%的数据异常）
        premium = calc_conversion_premium(price, stock_price, conv_price)
        if (premium is not None and premium <= discount_premium_threshold
                and premium >= -0.10  # 排除极端负溢价（数据异常）
                and conv_value is not None and conv_value >= 50):  # 排除正股暴跌废债
            disc_key = f"{code}_cb_discount_arb"
            if not base.already_notified(state, disc_key):
                discount_opps.append(
                    f"  {name}({code}) 现价{price:.2f} 溢价{premium*100:.1f}% | "
                    f"正股{stock_price} 转股价{conv_price} 转股价值{conv_value:.1f}\n"
                    f"    操作: 买入转债->当日转股->次日卖股 | 收益≈{abs(premium)*100:.1f}%"
                )
                base.mark_notified(state, disc_key, {"premium": f"{premium*100:.1f}%"})

        # 信号2: 到期保本套利（YTM > 5% 且 到期 < 1年）
        mat_days = days_to(maturity_date)
        if mat_days and 0 < mat_days <= maturity_days_max:
            exact_ytm = jsl_ytm_map.get(code)
            ytm = calc_ytm(price, maturity_date, exact_ytm)
            if ytm is not None and ytm >= ytm_threshold and price < ESTIMATED_REDEEM_PRICE:
                ytm_source = "精确" if exact_ytm is not None else "估算"
                mat_key = f"{code}_cb_maturity_arb"
                if not base.already_notified(state, mat_key):
                    maturity_opps.append(
                        f"  {name}({code}) 现价{price:.2f} YTM {ytm*100:.1f}%({ytm_source}) | "
                        f"到期{maturity_date}（剩{mat_days}天）\n"
                        f"    操作: 买入持有到期 | 到期赎回约¥{ESTIMATED_REDEEM_PRICE:.0f}"
                    )
                    base.mark_notified(state, mat_key, {"ytm": f"{ytm*100:.1f}%"})

    if discount_opps:
        alerts.append(
            "📈【折价转股套利】（买入转债+转股+次日卖股）\n"
            + "\n".join(discount_opps[:10])
            + "\n  ⚠️ 风险: 转股次日股票开盘可能低开，溢价<-2%安全垫更厚"
        )
    if maturity_opps:
        alerts.append(
            "💰【到期保本套利】（买入持有到期）\n"
            + "\n".join(maturity_opps[:10])
            + "\n  ✅ 确定性: 到期还本是法律义务，AA+以上违约概率<0.1%"
        )

    return alerts
