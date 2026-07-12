# 多品种套利机会监控器

## 项目用途

监控 QDII-LOF 折溢价、可转债、封闭基金的套利机会，通过 PushPlus 微信推送。
**核心定位**：只推"无底仓买入能赚钱的套利"，机器找机会，人判断可行性。

## 架构（2026-07-12 重构版）

```
GitHub Actions（A 股交易时段 3 次/天：10:00/12:00/14:00 北京时间）
  -> notify.py（统一推送入口）
    -> feedback.review_past_signals()  先回看历史信号实际收益
    -> monitors/qdii_lof.py            QDII-LOF P1/P2 折溢价套利
    -> monitors/sealed_fund.py         封闭基金折价年化
    -> monitors/convertible_bond.py    可转债到期保本套利
    -> PushPlus 微信推送
    -> state.json 去重（每信号每天最多 1 次）
    -> feedback.json 基线记录 + T+N 回看
  -> persist_state.py 通过 GitHub API PUT 持久化（绕过 git push 超时）

Streamlit 看板（app.py）- 被动浏览
  -> 3 个 tab：QDII-LOF / 可转债 / 封闭基金
```

## 推送信号设计（4 个套利信号，2026-07-12 精简）

所有信号都满足"用户无底仓，买入/申购能赚钱"的前提。

| 信号 | 条件 | 操作 | 确定性 | 说明 |
|------|------|------|--------|------|
| 可转债到期保本 | YTM>5% + 到期<1年 | 买入持有到期 | ⭐⭐⭐⭐ | 到期还本是法律义务，但高 YTM 本身在定价风险 |
| 封基折价年化 | 折价>3% 且 年化>4% | 买入持有到期/开放期 | ⭐⭐⭐⭐⭐ | 折价收敛是合同保证（NAV 本身不保证）|
| QDII P1 | 开放申购 + 溢价>5% | 申购->T+2卖出 | ⭐⭐⭐ | T+2 溢价崩塌风险 |
| QDII P2 | 折价>8% | 买入+赎回 | ⭐⭐⭐ | T+N 赎回 NAV 波动，大折价往往"有鬼" |

### 2026-07-12 重构删除的信号

- **QDII P0（申购恢复检测）**：原逻辑依赖 config.json 手动维护 buy_status，形成"用户改 config -> 系统通知用户"的循环通知。P1 已覆盖"开放申购+溢价"场景，P0 删除
- **要约收购套利**：低频事件（一年几次）+ 每单需人工判断"控股股东增持型 vs 实控人变更型"，自动化价值低。论坛抓取不可靠。如需恢复，建议接 LLM 读公告
- **可转债折价转股套利**：散户隔夜风险太大，安全垫覆盖不了正股波动（看板仍显示供参考）
- **可转债回售套利**：回售价非固定、30天条件难验证、公司可能下修（看板显示正股vs触发价供参考）

## 文件结构

| 文件 | 作用 |
|------|------|
| `app.py` | Streamlit 看板（3 tab） |
| `notify.py` | 统一推送入口，调各 monitor 的 check() + feedback 回看 |
| `monitors/base.py` | 公共工具：Sina实时价/天天基金估值/akshare净值/PushPlus/状态管理/基线记录/封基开放日抓取 |
| `monitors/qdii_lof.py` | QDII-LOF 折溢价监控（P1/P2） |
| `monitors/sealed_fund.py` | 封闭基金折价年化（双条件阈值，开放日自动抓取） |
| `monitors/convertible_bond.py` | 可转债到期保本套利 |
| `monitors/feedback.py` | 反馈层：T+N 回看历史信号实际收益 |
| `persist_state.py` | 通过 GitHub API 持久化 state.json + feedback.json |
| `config.json` | 监控列表 + 阈值 |
| `state.json` | 推送去重状态 |
| `feedback.json` | 基线记录 + 回看结果 |
| `.github/workflows/check.yml` | GitHub Actions 定时触发 |
| `REPORT.html` | 大白话总结报告 |

## 数据源

| 数据 | 源 | 接口 |
|------|----|------|
| 实时价格 | Sina | `hq.sinajs.cn/list=sz{code}` |
| 实时估值(IOPV) | 天天基金 | `fundgz.1234567.com.cn/js/{code}.js` |
| 净值历史 | akshare | `fund_open_fund_info_em` |
| 可转债精确YTM(30只) | akshare(集思录) | `bond_cb_jsl`（"到期税前收益"字段）|
| 可转债全面数据(320只) | akshare(集思录) | `bond_cb_redeem_jsl`（含到期日/强赎状态/转股价）|

**YTM 数据源策略（2026-07-12 精度修复）**：
- 主数据源：`bond_cb_jsl` 的"到期税前收益"字段（集思录精确计算，30只筛选转债）
- Fallback：`bond_cb_redeem_jsl` 320只，用保守估算 `(105-现价)/现价/剩余年限`
- 估算赎回价从 106 降为 105（保守，减少假阳性），且额外要求价格 < 105
- 推送文案标注"精确"或"估算"

**注意**：集思录已停止提供基金估值数据（LOF套利页面下线），但可转债/封闭基金数据仍可用。天天基金 IOPV 对 QDII 油气股类有效，原油期货类无数据。

## 重要约束

1. **PUSHPLUS_TOKEN 不硬编码**：通过 GitHub Secrets 传入
2. **每信号每天最多推 1 次**：state.json 按日期去重
3. **可转债排除退市转债**：代码 4 开头跳过
4. **YTM 双源策略**：优先 `bond_cb_jsl` 精确值，fallback 用 105 保守估算
5. **封基双条件阈值**：折价>3% 且 年化>4%（旧 8% 阈值在当前市场几乎不触发）
6. **封基开放日自动抓取**：`base.get_fund_open_date(code)` 从东方财富 F10 页面抓取预估开放申购起始日，config.json 无需手动维护 maturity_date
7. **封基异常数据过滤**：溢价 > 20% 视为净值滞后/分红除权，跳过（如 501046 曾出现 +37.81%）
8. **时区统一北京时间**：`base.now_beijing()` 替代 `datetime.now()`（GitHub Actions 默认 UTC）
9. **state.json + feedback.json 持久化用 `persist_state.py`**：GitHub API PUT，不回退到 git push（网络偶发超时）
10. **反馈层基线记录**：每次推送时 `base.record_baseline()` 写入 feedback.json，T+N 后 `feedback.review_past_signals()` 自动回看
11. **feedback.json completed 列表上限 100 条**：`review_past_signals` 自动清理旧记录
12. **只推无底仓能做的信号**：不推持有者防亏、不推投机、不推申购暂停时的溢价

## 常用命令

```bash
# 本地运行看板
cd /Users/bt/arbitrage-monitor
pip install -r requirements.txt
streamlit run app.py

# 手动扫描+推送（需设 PUSHPLUS_TOKEN 环境变量）
python notify.py

# 带 token 本地测试
PUSHPLUS_TOKEN="你的token" python notify.py

# 手动触发 GitHub Actions
gh workflow run check.yml -R xxdd3808-lgtm/arbitrage-monitor

# 查看运行日志
gh run list -R xxdd3808-lgtm/arbitrage-monitor --limit 5
```

## 配置 PUSHPLUS_TOKEN

已配置在 GitHub Secrets（2026-07-10）。如需更换：
```bash
gh secret set PUSHPLUS_TOKEN -R xxdd3808-lgtm/arbitrage-monitor
```

## 变更历史

### 2026-07-12 第一性原理重构

从第一性原理审视，修复 4 类问题：循环通知、空监控列表、YTM 估算误差、state 持久化。

**删除**：
- QDII P0 信号（循环通知：config 手动维护 -> 系统通知用户）
- 要约收购模块（低频 + 需人工分类，自动化价值低）
- 501050 错分类封基（ETF联接基金，非封基）
- 可转债折价转股/回售套利推送逻辑（保留看板参考）

**修复**：
- YTM 精度：主用 bond_cb_jsl 精确值，fallback 估算赎回价 106->105（保守）
- 封基列表：填充 13 只真实定开封基（161040/161132/161837/161914/162720/501046/501062/501070 等）
- 封基阈值：8% -> 双条件（折价>3% 且 年化>4%），贴合当前市场
- state.json 持久化：git push -> persist_state.py（GitHub API PUT，参考 dingshi-renwu）
- 时区统一：所有 datetime.now() -> base.now_beijing()（北京时间）

**新增**：
- 反馈层 monitors/feedback.py：推送时记录基线，T+N 自动回看实际收益
- persist_state.py：支持 state.json + feedback.json 多文件持久化
- base.record_baseline()：写入 feedback.json pending 列表

### 2026-07-10 信号精简为 6 个确定性套利（commit `2446820`）

基于网络调研验证（BigQuant研报+集思录实战+知乎CFA专栏）和用户反馈（"没底仓，只推买入能赚钱的"），大改：

- **可转债**：删掉4个旧信号（强赎/最后交易日/低溢价/到期保本粗判），改为2个确定性套利（折价转股溢价<-1% + 到期保本YTM>5%）
- **QDII**：删掉 P2 极端溢价>15%（已持有者卖出信号）
- **封基**：推送文案加流动性风险提示
- **过滤**：折价转股加极端负溢价(<-10%)+退市(4开头)+转股价值<50 三重过滤

### 2026-07-10 初版（commit `7512a88`）

- 新建 arbitrage-monitor 仓库，从 lof-monitor 单品种升级为多品种
- QDII-LOF 5只 + 可转债 320只 + 封闭基金
- 数据源改用 Sina+天天基金，不依赖 yfinance
- GitHub Actions 定时扫描 + PushPlus 推送 + state.json 去重
