---
name: mcd-combo-lab
description: 麦当劳「麦麦组合实验室」Skill。用官方 MCP 校验自创搭配是否真实可点、到底多少钱：拆解餐品的真实可定制空间（套餐子项替换 / 加价特制 / 减法开关），给出官方核价（原价与实付分开报），并生成可分享的单文件 HTML 配方卡。适用于「叠一个六层吉士堡」「这个组合能点吗」「换大薯条贵多少」「帮我算算我的搭配」等需求。
---

# 麦麦组合实验室 / McComboLab

用户爱折腾 DIY 搭配，但很多网络流传的「隐藏菜单」在官方系统里根本点不了，或者价格是猜的。本 Skill 用麦当劳官方 MCP 回答三件事：**能不能点、多少钱、现在这个时段有没有。**

## 何时使用

- 用户说：叠一个六层吉士堡、DIY 搭配核价、隐藏吃法、这个组合能点吗、帮我算算我的搭配
- 用户想创作并校验自创麦当劳搭配，或想知道替换/加价后贵多少

## 前置条件

- 麦当劳中国 MCP Token：<https://open.mcd.cn/mcp>
- 配置 `mcd-mcp`（见 `mcp-config.example.json`，Token 用环境变量 `MCD_MCP_TOKEN` 占位）
- 无 Token 时可执行 `python scripts/combo_lab.py demo` 跑通离线演示

## 核心原则

1. **不假装支持加料。** 官方 MCP 不提供加料能力（汉堡的可定制项全是 `price=0`、`maxQuantity=1` 的二值开关，菜单里没有牛肉饼/吉士片在售）。用户要求「六层吉士堡」时，必须说明官方不提供加料，并给出官方可核价的替代做法（按件叠搭），而不是含糊糊弄过去。
2. **原价与实付分开报。** `calculate-price` 的 `originalPrice` 是官方原价，`price` 是实付，`discount` 是**非券**的官方自动优惠。只报一个数必然报错。
3. **券是加购商品，不是折扣券。** 券不抵扣已有餐品金额，加购后总价必然 ≥ 配方原价。绝不能说「打几折」。
4. **营养基准只用麦当劳自家数据**，不引入外部膳食标准。
5. **完全只读**，不调 `create-order`。

## 标准工作流

### 第 1 步：采集搭配与门店

- 用户描述想怎么搭（如「两个吉士堡去酸黄瓜」「套餐换大薯条」「咖啡加浓缩」）
- 取门店：先试收藏门店（`searchType=1`），为空则要求城市 + 关键词走位置搜索（`searchType=2`）
- 提醒用户：单次只返回 5 家门店、无分页

### 第 2 步：拆解真实可定制空间

```
query-meals(storeCode)         → 校验每个组件是否在售（菜单随时段变化）
query-meal-detail(code)        → 拆解组成 / 可替换项 diffPrice / 特制项
```

- 若某组件不可售，明确说「这步点不了」并给替代
- 区分三类定制并如实告知价格影响：
  - **套餐子项替换**：真实进价（`diffPrice`）
  - **加价特制**：真实进价（`price>0`，实测仅咖啡有）
  - **减法特制**：真实可调但**不改价**

### 第 3 步：官方核价

```
query-store-coupons(storeCode, orderType, beType)  → 券（唯一带 couponId 的券源）
calculate-price(items)                              → 原价 / 实付 / 自动优惠
calculate-price(items + 券 item)                    → 加购后总价
```

券必须作为**独立 item** 传 `{productCode, quantity:1, couponId, couponCode}`；挂到普通餐品 item 上无效。

### 第 4 步：生成配方卡

```
python scripts/combo_lab.py card --store-code <门店> --recipe <配方>
```

单文件 HTML，内联 CSS，无外部依赖。

### 第 5 步：可分享

导出 HTML，附非官方声明与「不代下单」说明。

## 输出要求

- 每个组件必须标注「可售 / 不可售」，不可售要给替代
- 价格必须来自官方 `calculate-price`；同时给出原价与实付，自动优惠单独说明
- 每次输出含：非官方产品、以官方实时为准、不构成营养/医疗建议
- 营养若只匹配到部分子项，必须标注「已知 X 项」，不假装是整份真实合计

## 硬约束

- 配方必须基于真实在售餐品，不得虚构可点性
- 不跨品牌对比
- **不代用户下单**（不调用 `create-order`）
- 不输出真实 Token / 个人信息；配置仅用环境变量占位符
- 单 Token 每分钟不超过 600 次请求
- 配方库只存 `productCode` / `roundList` / `modification`，不存菜名与价格（会过期）

## 文件说明

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 本工作流定义 |
| `MCP_INTEGRATION.md` | MCP 接入说明 |
| `CONTEST_DECLARATION.md` | 参赛声明（官方文件，内容不可改） |
| `mcp-config.example.json` | 配置示例（仅环境变量占位符） |
| `scripts/combo_lab.py` | 业务引擎 |
| `references/live-contract.md` | 字段级契约，**改代码前必读** |
