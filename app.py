"""多品种套利机会监控器 - Streamlit 看板

Tabs:
1. QDII-LOF 实时对比（折溢价 + 申购赎回状态）
2. 可转债扫描（强赎/低溢价/到期保本）
3. 封闭基金扫描（折价年化排行）
"""

import streamlit as st
import akshare as ak
import pandas as pd
import requests
import re
import json
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitors import base
from monitors import sealed_fund as sf_mod

st.set_page_config(page_title="套利机会监控", page_icon="📊", layout="wide")
st.title("📊 多品种套利机会监控")
st.caption("QDII-LOF 折溢价 / 可转债 / 封闭基金 | 个人投资工具，不构成投资建议")

# 加载配置
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

tab1, tab2, tab3 = st.tabs(["🛢️ QDII-LOF", "债券 可转债", "📊 封闭基金"])


# ---------- Tab 1: QDII-LOF ----------
with tab1:
    st.subheader("QDII-LOF 实时折溢价对比")
    st.caption("实时价格(Sina) / 实时估值(天天基金) 或 最新净值(akshare)")

    @st.cache_data(ttl=60, show_spinner=False)
    def fetch_qdii_data():
        results = []
        for item in CONFIG.get("qdii_lof", []):
            code = item["code"]
            row = {**item}
            price_data = base.get_realtime_price_sina(code)
            valuation = base.get_fund_realtime_valuation(code)
            row["price"] = price_data["last"] if price_data else None
            row["price_name"] = price_data["name"] if price_data else ""
            if valuation and valuation.get("gsz"):
                row["iopv"] = valuation["gsz"]
                row["iopv_source"] = f"实时估值({valuation['gztime'][-5:]})"
                row["nav"] = valuation.get("nav")
                row["nav_date"] = valuation.get("nav_date")
            else:
                nav, nav_date = base.get_fund_latest_nav(code)
                row["iopv"] = nav
                row["iopv_source"] = f"净值({nav_date})" if nav_date else ""
                row["nav"] = nav
                row["nav_date"] = nav_date
            if row["price"] and row["iopv"] and row["iopv"] > 0:
                row["premium"] = row["price"] / row["iopv"] - 1
            else:
                row["premium"] = None
            results.append(row)
        return results

    qdii_data = fetch_qdii_data()

    rows = []
    for r in qdii_data:
        premium_str = f"{r['premium']*100:+.2f}%" if r["premium"] is not None else "N/A"
        rows.append({
            "代码": r["code"],
            "名称": r["name"],
            "场内价": f"¥{r['price']:.4f}" if r["price"] else "N/A",
            "IOPV": f"¥{r['iopv']:.4f}" if r["iopv"] else "N/A",
            "IOPV来源": r["iopv_source"],
            "折溢价": premium_str,
            "申购": r.get("buy_status", "?"),
            "赎回": r.get("redeem_status", "?"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("💡 可操作标的")
    col_a, col_b = st.columns(2)
    with col_a:
        st.write("**P1: 开放申购 + 溢价 > 5%**（申购->T+2卖出）")
        found = False
        for r in qdii_data:
            if r["premium"] and r["premium"] > 0.05 and "开放" in r.get("buy_status", ""):
                st.write(f"- {r['name']}({r['code']}) 溢价 {r['premium']*100:+.2f}%")
                found = True
        if not found:
            st.write("（暂无，QDII普遍暂停申购）")
    with col_b:
        st.write("**P2: 折价 > 8%**（买入+赎回安全垫厚）")
        found = False
        for r in qdii_data:
            if r["premium"] and r["premium"] < -0.08:
                st.write(f"- {r['name']}({r['code']}) 折价 {r['premium']*100:+.2f}% | 赎回:{r.get('redeem_status','?')}")
                found = True
        if not found:
            st.write("（暂无）")

    st.caption("注：申购赎回状态来自 config.json 预设。状态变化时 notify.py 会自动推送 P0 信号。")


# ---------- Tab 2: 可转债 ----------
with tab2:
    st.subheader("可转债扫描")
    st.caption("数据源：akshare bond_cb_redeem_jsl（集思录）")

    cb_config = CONFIG.get("convertible_bond", {})

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_cb_data():
        try:
            df = ak.bond_cb_redeem_jsl()
            return df
        except Exception as e:
            st.error(f"数据获取失败: {e}")
            return None

    with st.spinner("拉取可转债数据..."):
        cb_df = fetch_cb_data()

    if cb_df is not None and len(cb_df) > 0:
        st.metric("可转债总数", len(cb_df))

        # 强赎中的转债
        st.markdown("---")
        st.subheader("⚠️ 强赎中（已公告强赎）")
        redeemed = cb_df[cb_df["强赎状态"].astype(str).str.contains("已公告", na=False)].copy()
        if len(redeemed) > 0:
            redeemed["距最后交易日(天)"] = redeemed["最后交易日"].apply(
                lambda x: sf_mod.days_to(x) if x else None
            )
            show = redeemed[["代码", "名称", "现价", "最后交易日", "距最后交易日(天)", "强赎状态"]].copy()
            show = show.sort_values("距最后交易日(天)")
            st.dataframe(show, use_container_width=True, hide_index=True)
        else:
            st.write("（暂无）")

        # 低溢价+低价
        st.markdown("---")
        st.subheader(f"📈 低溢价机会（溢价<{cb_config.get('low_premium',0.05)*100:.0f}% 且 价格<{cb_config.get('low_premium_price_max',115)}）")
        low_pm = cb_df.copy()
        # 计算溢价率（如果没有直接字段）
        low_pm["计算溢价率"] = low_pm.apply(
            lambda r: sf_mod.calc_conversion_premium(
                r.get("现价"), r.get("正股价"), r.get("转股价")
            ), axis=1
        )
        lp_threshold = cb_config.get("low_premium", 0.05)
        lp_price_max = cb_config.get("low_premium_price_max", 115)
        mask = (low_pm["计算溢价率"] <= lp_threshold) & (low_pm["现价"] <= lp_price_max) & (low_pm["计算溢价率"].notna())
        filtered = low_pm[mask & ~low_pm["强赎状态"].astype(str).str.contains("已公告", na=False)]
        if len(filtered) > 0:
            show = filtered[["代码", "名称", "现价", "正股价", "转股价", "计算溢价率", "强赎状态"]].copy()
            show["计算溢价率"] = show["计算溢价率"].apply(lambda x: f"{x*100:.1f}%")
            show = show.sort_values("现价")
            st.dataframe(show.head(20), use_container_width=True, hide_index=True)
        else:
            st.write("（暂无）")

        # 到期保本
        st.markdown("---")
        st.subheader(f"💰 到期保本（价格<{cb_config.get('maturity_price_max',105)} 且 剩余<{cb_config.get('maturity_days',365)}天）")
        mat_price_max = cb_config.get("maturity_price_max", 105)
        mat_days_max = cb_config.get("maturity_days", 365)
        cb_df["距到期(天)"] = cb_df["到期日"].apply(lambda x: sf_mod.days_to(x) if x else None)
        mat_mask = (cb_df["现价"] <= mat_price_max) & (cb_df["距到期(天)"].notna()) & (cb_df["距到期(天)"] > 0) & (cb_df["距到期(天)"] <= mat_days_max)
        mat_filtered = cb_df[mat_mask]
        if len(mat_filtered) > 0:
            show = mat_filtered[["代码", "名称", "现价", "到期日", "距到期(天)"]].copy()
            show = show.sort_values("距到期(天)")
            st.dataframe(show.head(20), use_container_width=True, hide_index=True)
        else:
            st.write("（暂无）")


# ---------- Tab 3: 封闭基金 ----------
with tab3:
    st.subheader("封闭基金折价年化排行")
    st.caption("折价年化 = |折价率| / 剩余年限。年化越高，持有到期的年化收益越高。")

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_sf_data():
        results = []
        for item in CONFIG.get("sealed_fund", []):
            code = item["code"]
            name = item["name"]
            maturity = item.get("maturity_date", "")
            row = {**item}
            price_data = base.get_realtime_price_sina(code)
            nav, nav_date = base.get_fund_latest_nav(code)
            row["price"] = price_data["last"] if price_data else None
            row["nav"] = nav
            row["nav_date"] = nav_date
            if row["price"] and row["nav"] and row["nav"] > 0:
                row["discount"] = row["price"] / row["nav"] - 1
            else:
                row["discount"] = None
            row["annualized"] = sf_mod.calc_annualized_discount(row["discount"], maturity)
            results.append(row)
        return results

    sf_data = fetch_sf_data()

    if sf_data:
        rows = []
        for r in sf_data:
            disc_str = f"{r['discount']*100:+.2f}%" if r["discount"] is not None else "N/A"
            ann_str = f"{r['annualized']*100:.1f}%" if r["annualized"] else "N/A"
            rows.append({
                "代码": r["code"],
                "名称": r["name"],
                "场内价": f"¥{r['price']:.4f}" if r["price"] else "N/A",
                "净值": f"¥{r['nav']:.4f}" if r["nav"] else "N/A",
                "折价": disc_str,
                "到期日": r.get("maturity_date", ""),
                "折价年化": ann_str,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("注：封闭基金列表在 config.json 中维护。如需添加更多封基，编辑 config.json 的 sealed_fund 字段。")
    else:
        st.info("config.json 中未配置封闭基金。")
