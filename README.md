# 高抛低吸 · 做价差飞书通知机器人

针对 **A股被套个股**，在 **震荡行情** 中按网格自动监控价格，触发 **高抛 / 低吸** 信号并推送到 **飞书群**。
通过反复在高位卖出、低位买回赚取格差，从而 **摊薄持仓成本**、解套降本。

> ⚠️ 适用于震荡市；**单边行情会使做价差失效**（单边上涨踏空、单边下跌越买越套）。本工具内置「单边行情预警」，但买卖决策仍需人工研判。本项目仅为信息提醒，**不构成投资建议**。

---

## 策略原理

默认使用网格做价差；也可以把单只股票的 `strategy` 改为 `confluence`，启用 **Retracement + 趋势线（line chart）+ 支阻互换 + VWAP** 共振策略。

### 网格策略

以 **基准价（中枢）** 为中心，按固定百分比（每格 `grid_step_pct`）划分网格：

```
   ↑ 价格
  ─────────  +2 格   ← 涨到这里：高抛（卖出 1 份）
  ─────────  +1 格   ← 涨到这里：高抛（卖出 1 份）
  ═════════   0 格   ← 基准价（中枢）
  ─────────  -1 格   ← 跌到这里：低吸（买回 1 份）
  ─────────  -2 格   ← 跌到这里：低吸（买回 1 份）
```

- **价格上穿一格 → 🔴 高抛**：在相对高位卖出 `trade_shares` 股，落袋筹码；
- **价格下穿一格 → 🟢 低吸**：在相对低位买回 `trade_shares` 股，等待下一轮；
- 一卖一买完成一轮「价差」，赚取约 `每格价差 × 股数`，**摊低成本**。

**抗抖动**：采用整格回滞，必须完整越过相邻一格才触发，避免在格线附近反复刷信号；一次大幅跳空可跨多格，按实际格数给出建议股数。

**单边预警**：统计同向连续格数，连续 `trend_alert_grids`（默认 3）格同向时，在卡片中提示疑似单边行情。

### 共振策略（Retracement + 趋势线 + 支阻互换 + VWAP）

把某只股票配置为 `strategy: "confluence"` 后，程序会拉取腾讯当日分时线，并对最近 `confluence_lookback` 个分时点做四项判断：

- **Retracement**：上涨后的回撤，或下跌后的反抽，落在 `retracement_min_pct` ~ `retracement_max_pct` 区间；
- **趋势线**：用 line chart 的局部高/低点拟合下降压力线或上升支撑线，价格来到趋势线附近；
- **支阻互换**：前压力突破后回踩成支撑，或前支撑跌破后反抽成压力；
- **VWAP**：价格与当日均价/VWAP 的相对位置配合方向。

默认至少满足 `confluence_min_score: 3` 项才推送，卡片会展示命中的共振理由。该策略仍只做提醒，不自动下单。

---

## 目录结构

```
vertical_stock/
├── main.py                 # CLI 入口：run / loop / status / test
├── config.example.yaml     # 配置模板（复制为 config.yaml 使用）
├── requirements.txt        # 仅依赖 PyYAML
├── spread_bot/
│   ├── config.py           # 配置加载与校验
│   ├── quotes.py           # 实时行情与腾讯分时线（腾讯主 / 新浪备，自动回退）
│   ├── strategy.py         # 网格做价差引擎（信号 / 回滞 / 单边预警）
│   ├── confluence.py       # 回撤 + 趋势线 + 支阻互换 + VWAP 共振策略
│   ├── state.py            # 运行状态持久化（原子写）
│   ├── market.py           # 交易所推断 / 涨跌停 / 交易时段
│   └── notifier.py         # 飞书交互卡片推送（支持加签）
└── deploy/                 # 火山引擎部署：systemd / cron / 包装脚本
```

---

## 快速开始

```bash
# 1) 安装依赖（仅一个）
pip3 install -r requirements.txt

# 2) 准备配置
cp config.example.yaml config.yaml
#   编辑 config.yaml：填入飞书 webhook、添加你的持仓股票

# 3) 测试飞书连通性
python3 main.py test

# 4) 查看当前网格态势（不下单、不推送）
python3 main.py status

# 5) 单次评估（休市时加 --force 可强制跑一次看效果）
python3 main.py run --dry-run --force     # 只打印，不推送、不写状态
python3 main.py run --force               # 真实推送

# 6) 常驻运行（自动按交易时段轮询）
python3 main.py loop
```

### 子命令

| 命令 | 说明 |
|------|------|
| `run` | 单次评估并推送信号，适合 cron 定时调用 |
| `loop` | 常驻循环，按 `poll_interval_seconds` 轮询，适合 systemd 守护 |
| `status` | 打印各标的网格态势（基准、当前格、下一高抛/低吸价）；加 `--notify` 推送状态卡 |
| `test` | 发送一张测试卡片，校验 webhook |

公共参数：`--config <路径>`（默认 `config.yaml`）、`--dry-run`（只评估不推送不写状态）、`--force`（忽略交易时段限制）。

---

## 配置说明（`config.yaml`）

```yaml
webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"  # 飞书自定义机器人地址
feishu_secret: ""           # 机器人开启「加签」时填密钥，否则留空
data_source: "tencent"      # tencent（默认）| sina（备用，主源失败自动回退）
poll_interval_seconds: 60   # loop 轮询间隔（秒）
only_trading_hours: true    # 仅交易时段运行
state_file: "state.json"    # 状态文件（相对路径相对本配置解析）

stocks:
  - code: "300763"          # 必填：6 位代码，沪/深/北自动识别
    name: "锦浪科技"        # 可选：留空自动取行情名称
    cost: 130.0             # 可选：持仓成本，仅展示「距成本/浮亏」
    base_price: null        # 网格中枢：填具体值=立即按此评估；null=以昨收自动锚定
    grid_step_pct: 3.0      # 每格价差%：越小越灵敏、信号越多
    trade_shares: 200       # 每格建议交易股数（100 股=1 手）
    upper_limit_pct: 30     # 网格上限（相对中枢%），超出提示停止高抛；留空不限
    lower_limit_pct: -30    # 网格下限（相对中枢%），触及提示停止低吸；留空不限
    trend_alert_grids: 3    # 连续同向 N 格触发单边预警
    strategy: "grid"        # grid | confluence
    confluence_min_score: 3 # confluence 下四项共振至少满足几项
```

### `base_price`（网格中枢）怎么填

- **填具体值**：网格立即以该价为中枢评估，首次即可能触发。适合你心里有明确的高抛低吸中枢价。
- **留空 `null`**：以昨收价自动锚定，首次仅记录不触发，之后按格波动推送。适合「让程序自己找中枢」。
- **修改中枢**：直接改 `config.yaml` 里的 `base_price` 即可，程序检测到变化会自动按新中枢重锚（无需手动删状态）。若把它从具体值改回 `null`，则需删除 `state.json` 该股条目使其重新自动锚定。

> 调参建议：震荡区间清晰的票，`grid_step_pct` 取 2~5%；波动大可放宽。`trade_shares` 按你愿意每格动用的仓位定。

---

## 飞书机器人设置

1. 目标群 → 设置 → 群机器人 → 添加机器人 → **自定义机器人**；
2. 复制 **Webhook 地址** 填入 `config.yaml` 的 `webhook`；
3. 安全设置若选 **签名校验**，把密钥填入 `feishu_secret`（本工具已支持加签）。

---

## 部署到火山引擎云服务器

在火山引擎 ECS（CentOS/Ubuntu 均可）上：

```bash
# 上传代码后
cd /opt/vertical_stock
pip3 install -r requirements.txt
cp config.example.yaml config.yaml && vi config.yaml   # 填 webhook 与持仓
python3 main.py test                                   # 验证连通
```

### 方式一：systemd 常驻（推荐）

```bash
sudo cp deploy/spread-bot.service /etc/systemd/system/   # 按需改路径/用户
sudo systemctl daemon-reload
sudo systemctl enable --now spread-bot
journalctl -u spread-bot -f                              # 看日志
```

### 方式二：cron 定时单次

```bash
crontab -e        # 粘贴 deploy/crontab.example 内容（改成实际路径）
# 交易时段每 2 分钟评估一次，收盘后推送当日态势总览
```

> 时区：确保服务器时区为 **Asia/Shanghai**（`timedatectl set-timezone Asia/Shanghai`），否则交易时段判断会错。

---

## 注意事项 / 风险提示

- **节假日**：交易时段判断仅排除周末，未含法定休市日。休市时行情不变化，不会误触发信号；如需严格控制可在外层用交易日历过滤。
- **ST 股**：±5% 涨跌停限制无法仅凭代码识别，涨跌停价按板块默认值估算。
- **单边市场**：做价差的最大风险——单边上涨易踏空、单边下跌越买越套。请重视卡片中的 ⚠️ 单边预警，必要时暂停策略或保留底仓。
- **数据源**：使用腾讯/新浪公开行情，仅供参考，可能有延迟或偶发不可用（已做主备自动回退）。
- 本项目为 **信息提醒工具**，所有买卖由你人工决策执行，**风险自负，不构成投资建议**。
