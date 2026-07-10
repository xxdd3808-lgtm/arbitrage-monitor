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
from monitors import convertible_bond as cb_mod

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
    st.subheader("可转债确定性套利扫描")
    st.caption("只显示买入能赚钱的机会：折价转股套利 + 到期保本套利")

    cb_config = CONFIG.get("convertible_bond", {})
    ytm_threshold = cb_config.get("ytm_threshold", 0.05)
    discount_threshold = cb_config.get("discount_premium", -0.01)
    mat_days_max = cb_config.get("maturity_days", 365)

    @st.cache_data(ttl=300, show_spinner=False)
    def fetch_cb_data():
        import akshare as ak
        dfs = {}
        try:
            dfs["redeem"] = ak.bond_cb_redeem_jsl()
        except Exception:
            pass
        try:
            dfs["jsl"] = ak.bond_cb_jsl()
        except Exception:
            pass
        return dfs

    with st.spinner("拉取可转债数据..."):
        cb_dfs = fetch_cb_data()

    cb_df = cb_dfs.get("redeem")
    jsl_df = cb_dfs.get("jsl")

    if cb_df is not None and len(cb_df) > 0:
        st.metric("可转债总数", len(cb_df))

        # 构建精确YTM映射（bond_cb_jsl 30只）
        jsl_ytm_map = {}
        if jsl_df is not None:
            for _, row in jsl_df.iterrows():
                code = str(row.get("代码", "")).strip()
                ytm_raw = row.get("到期税前收益")
                try:
                    ytm = float(ytm_raw) / 100 if ytm_raw is not None else None
                    if code and ytm is not None and -0.5 < ytm < 0.5:
                        jsl_ytm_map[code] = ytm
                except (TypeError, ValueError):
                    pass

        # 计算溢价率和转股价值
        cb_df = cb_df.copy()
        cb_df["转股价值"] = cb_df.apply(
            lambda r: cb_mod.calc_conversion_value(r.get("正股价"), r.get("转股价")), axis=1
        )
        cb_df["溢价率"] = cb_df.apply(
            lambda r: cb_mod.calc_conversion_premium(r.get("现价"), r.get("正股价"), r.get("转股价")), axis=1
        )
        cb_df["距到期(天)"] = cb_df["到期日"].apply(cb_mod.days_to)
        cb_df["代码"] = cb_df["代码"].astype(str)

        # 信号1: 折价转股套利（溢价 < -1%）
        st.markdown("---")
        st.subheader(f"📈 折价转股套利（溢价 < {discount_threshold*100:.0f}%）")
        st.caption("操作: 买入转债->当日转股->次日卖股 | ⚠️ 隔夜风险")
        disc_mask = (
            (cb_df["溢价率"].notna())
            & (cb_df["溢价率"] <= discount_threshold)
            & (cb_df["溢价率"] >= -0.10)  # 排除极端负溢价（数据异常）
            & (~cb_df["代码"].str.startswith("4"))  # 排除退市
            & (cb_df["转股价值"] >= 50)  # 排除废债
        )
        disc_filtered = cb_df[disc_mask]
        if len(disc_filtered) > 0:
            show = disc_filtered[["代码", "名称", "现价", "转股价值", "溢价率", "正股价", "转股价"]].copy()
            show["溢价率"] = show["溢价率"].apply(lambda x: f"{x*100:.1f}%")
            show["转股价值"] = show["转股价值"].apply(lambda x: f"{x:.1f}")
            show = show.sort_values("溢价率")
            st.dataframe(show.head(20), use_container_width=True, hide_index=True)
        else:
            st.write("（暂无）")

        # 信号2: 到期保本套利（YTM > 5% 且 到期 < 1年）
        st.markdown("---")
        st.subheader(f"💰 到期保本套利（YTM > {ytm_threshold*100:.0f}% 且 到期 < {mat_days_max}天）")
        st.caption("操作: 买入持有到期 | ✅ 到期还本是法律义务，确定性高")
        mat_mask = (
            (cb_df["距到期(天)"].notna())
            & (cb_df["距到期(天)"] > 0)
            & (cb_df["距到期(天)"] <= mat_days_max)
            & (~cb_df["代码"].str.startswith("4"))
        )
        mat_filtered = cb_df[mat_mask].copy()
        if len(mat_filtered) > 0:
            # 计算YTM
            mat_filtered["精确YTM"] = mat_filtered["代码"].map(jsl_ytm_map)
            mat_filtered["YTM"] = mat_filtered.apply(
                lambda r: cb_mod.calc_ytm(r["现价"], r["到期日"], r.get("精确YTM")), axis=1
            )
            mat_filtered["YTM来源"] = mat_filtered["精确YTM"].apply(lambda x: "精确" if x is not None else "估算")
            ytm_mask = (mat_filtered["YTM"].notna()) & (mat_filtered["YTM"] >= ytm_threshold) & (mat_filtered["现价"] < 106)
            ytm_filtered = mat_filtered[ytm_mask]
            if len(ytm_filtered) > 0:
                show = ytm_filtered[["代码", "名称", "现价", "到期日", "距到期(天)", "YTM", "YTM来源"]].copy()
                show["YTM"] = show["YTM"].apply(lambda x: f"{x*100:.1f}%")
                show = show.sort_values("YTM", ascending=False)
                st.dataframe(show.head(20), use_container_width=True, hide_index=True)
            else:
                st.write("（暂无）")
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
