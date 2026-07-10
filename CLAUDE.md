# 多品种套利机会监控器

## 项目用途

监控 QDII-LOF 折溢价、可转债、封闭基金的套利机会，通过 PushPlus 微信推送。
从"发现机会 -> 判断可操作性 -> 推送提醒"全链路辅助低风险投资决策。

**核心定位**：不推没用的信号（普通折价3-5%不赚钱、溢价但申购暂停做不了），只推"可操作的组合信号"。

## 架构

```
GitHub Actions（A 股交易时段 3 次/天）
  -> notify.py（统一推送入口）
    -> monitors/qdii_lof.py     QDII-LOF 折溢价 + 申购状态变化
    -> monitors/sealed_fund.py  封闭基金折价年化
    -> monitors/convertible_bond.py  可转债强赎/低溢价/到期保本
    -> PushPlus 微信推送
    -> state.json 去重（每信号每天最多 1 次）

Streamlit 看板（app.py）- 被动浏览
  -> 3 个 tab：QDII-LOF / 可转债 / 封闭基金
```

## 推送信号设计

### QDII-LOF
| 信号 | 条件 | 优先级 |
|------|------|--------|
| 申购恢复+溢价 | 申购 暂停->开放 且 溢价>3% | P0 黄金 |
| 开放申购+持续溢价 | 开放申购 且 溢价>5% | P1 |
| 极端折价 | 折价>8% | P2 |
| 极端溢价 | 溢价>15% | P2 |

### 可转债
| 信号 | 条件 | 优先级 |
|------|------|--------|
| 新强赎公告 | 强赎状态变为"已公告强赎" | P1 |
| 强赎最后交易日临近 | 已公告强赎 且 最后交易日<30天 | P1 |
| 到期保本 | 现价<105 且 到期<365天 | P2 |
| 低溢价机会 | 溢价<5% 且 现价<115 | P2 |

### 封闭基金
| 信号 | 条件 | 优先级 |
|------|------|--------|
| 折价年化高 | 折价年化>8% | P1 |

## 文件结构

| 文件 | 作用 |
|------|------|
| `app.py` | Streamlit 看板（3 tab） |
| `notify.py` | 统一推送入口，调各 monitor 的 check() |
| `monitors/base.py` | 公共工具：Sina实时价/天天基金估值/akshare净值/PushPlus/状态管理 |
| `monitors/qdii_lof.py` | QDII-LOF 折溢价 + 申购状态监控 |
| `monitors/sealed_fund.py` | 封闭基金折价年化 |
| `monitors/convertible_bond.py` | 可转债多信号 |
| `config.json` | 监控列表 + 阈值 + 申购赎回状态预设 |
| `state.json` | 推送去重状态 |
| `.github/workflows/check.yml` | GitHub Actions 定时触发 |

## 数据源

| 数据 | 源 | 接口 |
|------|----|------|
| 实时价格 | Sina | `hq.sinajs.cn/list=sz162411` |
| 实时估值(IOPV) | 天天基金 | `fundgz.1234567.com.cn/js/{code}.js` |
| 净值历史 | akshare | `fund_open_fund_info_em` |
| 可转债强赎 | akshare(集思录) | `bond_cb_redeem_jsl` |
| 申购赎回状态 | config.json 预设 | 手动维护 |

**关键发现**：天天基金实时估值 API 对 QDII 油气股类 LOF 有效（华宝油气/广发石油），但对原油期货类 LOF 无数据（南方/嘉实/易方达原油）。后者用 settled 折溢价（场内价/最新净值-1），有滞后但可用。

## 重要约束

1. **PUSHPLUS_TOKEN 不硬编码**：通过 GitHub Secrets 传入
2. **申购赎回状态用 config.json 预设**：自动 API 检测不可靠（东财移动 API 全 404），用 config.json 手动维护 + state.json 对比检测变化
3. **每信号每天最多推 1 次**：state.json 按日期去重
4. **不推无操作价值的信号**：普通折价3-5%（回测证明不赚钱）、普通溢价5-8%但申购暂停（做不了）
5. **可转债用 bond_cb_redeem_jsl**：集思录数据最全，含强赎状态/最后交易日/到期日

## 常用命令

```bash
# 本地运行看板
cd /Users/bt/arbitrage-monitor
pip install -r requirements.txt
streamlit run app.py

# 手动触发推送检查
python notify.py

# 手动触发 GitHub Actions
gh workflow run check.yml -R xxdd3808-lgtm/arbitrage-monitor

# 查看运行日志
gh run list -R xxdd3808-lgtm/arbitrage-monitor --limit 5
```

## 配置 PUSHPLUS_TOKEN

1. 去 [pushplus.plus](https://www.pushplus.plus/) 注册，获取 token
2. GitHub 仓库 Settings -> Secrets and variables -> Actions -> New repository secret
   - Name: `PUSHPLUS_TOKEN`
   - Value: 你的 token
3. 或用命令行：`gh secret set PUSHPLUS_TOKEN -R xxdd3808-lgtm/arbitrage-monitor`

## 变更历史

### 2026-07-10 初版（从 lof-monitor 升级）

- 新建 arbitrage-monitor 仓库，从单品种(华宝油气)升级为多品种
- QDII-LOF：5只监控，P0/P1/P2 组合信号，Sina实时价+天天基金估值
- 可转债：bond_cb_redeem_jsl 数据源，4类信号（强赎/最后交易日/到期保本/低溢价）
- 封闭基金：折价年化公式，config 预设列表
- 推送逻辑改为"组合信号"（申购恢复+溢价、开放+溢价），不再推无操作价值的单独信号
- 数据源改用 Sina+天天基金，不依赖 yfinance（本地和 GitHub Actions 均稳定）
