"""可转债到期保本套利监控

推送信号：
- 到期保本套利：YTM > 5% 且 到期 < 1年
  操作：买入持有到期
  收益：(到期赎回价 - 现价) / 现价，年化
  确定性：到期还本是法律义务

YTM 数据源（2026-07-12 精度修复）：
- 主数据源：bond_cb_jsl 的"到期税前收益"字段（集思录精确计算，30只筛选转债）
- Fallback：bond_cb_redeem_jsl 的 320 只，用保守估算 (105-现价)/现价/剩余年限
  - 估算赎回价从 106 降为 105（保守，减少假阳性）
  - 估算结果仅作候选，推送时标注"估算"

注意：以下信号已降级为看板参考，不推送：
- 折价转股套利：散户隔夜风险太大（app.py 看板仍显示供参考）
- 回售套利：回售价非固定、30天条件难验证、公司可能下修（app.py 看板显示正股vs触发价）

数据源：
- bond_cb_jsl (30只，精确YTM)
- bond_cb_redeem_jsl (320只，全面扫描)
"""

from datetime import datetime
from . import base

# Fallback 估算赎回价（保守，多数转债 105-110，用 105 减少假阳性）
FALLBACK_REDEEM_PRICE = 105.0


def calc_conversion_premium(bond_price, stock_price, conversion_price):
    """转股溢价率 = 债现价/转股价值 - 1。负数=折价"""
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
        now = base.now_beijing()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        return (d - now).days
    except (ValueError, TypeError):
        return None


def calc_ytm(price, maturity_date, exact_ytm=None):
    """计算到期收益率(YTM)。
    优先用集思录精确值，否则用 (105-现价)/现价/剩余年限 保守估算。
    返回小数（0.05 = 5%）"""
    if exact_ytm is not None and -0.5 < exact_ytm < 0.5:  # 过滤异常值
        return exact_ytm
    days = days_to(maturity_date)
    if not days or days <= 0 or price <= 0:
        return None
    years = days / 365.0
    return (FALLBACK_REDEEM_PRICE - price) / price / years


def check(config, state):
    alerts = []
    cb_config = config.get("convertible_bond", {})
    ytm_threshold = cb_config.get("ytm_threshold", 0.05)
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
                if code and ytm is not None and -0.5 < ytm < 0.5:
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

    maturity_opps = []

    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        name = str(row.get("名称", "")).strip()
        price = row.get("现价")
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

        # 信号: 到期保本套利（YTM > 5% 且 到期 < 1年）
        mat_days = days_to(maturity_date)
        if mat_days and 0 < mat_days <= maturity_days_max:
            exact_ytm = jsl_ytm_map.get(code)
            ytm = calc_ytm(price, maturity_date, exact_ytm)
            ytm_source = "精确" if exact_ytm is not None else "估算"
            # 估算的额外要求价格 < 105（保守，避免假阳性）；精确的信任集思录
            price_cap = FALLBACK_REDEEM_PRICE if exact_ytm is None else 110
            if ytm is not None and ytm >= ytm_threshold and price < price_cap:
                mat_key = f"{code}_cb_maturity_arb"
                if not base.already_notified(state, mat_key):
                    maturity_opps.append(
                        f"  {name}({code}) 现价{price:.2f} YTM {ytm*100:.1f}%({ytm_source}) | "
                        f"到期{maturity_date}（剩{mat_days}天）\n"
                        f"    操作: 买入持有到期 | 到期赎回约¥{FALLBACK_REDEEM_PRICE:.0f}(估算)/实际见公告"
                    )
                    base.mark_notified(state, mat_key, {"ytm": f"{ytm*100:.1f}%", "source": ytm_source})
                    base.record_baseline(state, code, "cb_maturity", price)

    if maturity_opps:
        alerts.append(
            "💰【到期保本套利】（买入持有到期）\n"
            + "\n".join(maturity_opps[:10])
            + "\n  ✅ 确定性: 到期还本是法律义务（精确YTM来自集思录，估算为保守推算）"
        )

    return alerts
