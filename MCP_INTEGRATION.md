# MCP 接入说明 / `mcd-combo-lab`

## MCP Server

- 名称：`mcd-mcp`
- 接入地址：`https://mcp.mcd.cn`
- 协议：Streamable HTTP
- 鉴权：`Authorization: Bearer ${MCD_MCP_TOKEN}`（Token 通过环境变量注入，禁止硬编码）
- 实测：单头 `Authorization` 即通，本项目涉及的工具**均不需要** `authToken` 头
- 限流：单 Token 600 次/分钟

## 使用的工具

| 工具 | 在本项目中的作用 |
|---|---|
| `query-nearby-stores` | 双路获取门店：收藏优先（`searchType=1`），为空则位置搜索（`searchType=2`） |
| `query-meals` | 拉取门店当前在售菜单，判定配方组件的可售性 |
| `query-meal-detail` | 拆解餐品真实组成（`rounds`）与可定制空间（`modification` / `choices`） |
| `calculate-price` | 官方核价：原价、实付、自动优惠、用券 |
| `query-store-coupons` | 门店可用券（**三个券工具中唯一返回结构化 `couponId` 的**） |
| `list-nutrition-foods` | 官方营养表，用于真实子项聚合与分位基准 |
| `now-time-info` | 取麦当劳服务器时间，用于判定当前餐段 |

**不使用的写操作**：`create-order` / `party-order-create` / `mall-create-order` 全部不接入，本项目完全只读。

## 调用流程

```
query-nearby-stores          取门店（收藏优先 → 位置回退）
        ↓
query-meals                  拉当前在售菜单（随时段变化）
query-meal-detail            拆组件：可替换项 diffPrice / 特制项 price
        ↓
verify                       复核：在售性 + 当前餐段（now-time-info）+ 可替换项
        ↓
query-store-coupons          取门店可用券（结构化 couponId）
calculate-price              核价：原价 / 实付 / 自动优惠 / 加购券三档
        ↓
list-nutrition-foods         营养：按真实子项聚合 + 本店在售分布分位
        ↓
build_card.py                渲染单文件 HTML 配方卡
```

## 业务价值

网络上的「隐藏吃法」大多是口口相传，没人验证过能不能点、多少钱。本 Skill 把分散在 7 个 MCP 工具里的真实数据，收敛成一个可复核、可分享的**组合决策**：

1. **证伪**：先说清楚官方不提供加料，避免用户照着网传配方点了却点不到
2. **定价**：把「大概二三十块」变成官方 `calculate-price` 给出的原价与实付
3. **可点性**：结合门店 `reservationTimeOptions` 与服务器时间，判断当前餐段是否还点得到

## 关键实测契约（摘选，完整版见 `references/live-contract.md`）

- **`isError` 恒为 `false`**：业务失败藏在 `structuredContent.success=false` + `code`/`message`，必须先校验 `success`
- **Token 失效 → HTTP 401 + 纯文本响应体**，需单独分支，不要 JSON 解析
- **单位**：`query-meals` 价格是**元**（字符串）；`calculate-price` 全部金额是**分**（整数）
- **核价响应三类定制**：加价特制（咖啡加浓缩 +¥5）与套餐子项替换（换大薯条 +¥2.5）真实进价；减法特制不改价
- **原价 ≠ 实付**：`discount` 会带出**非券**的官方自动优惠（实测 ¥7.10）
- **券是加购商品**：必须作为独立 item 传 `{productCode, quantity:1, couponId, couponCode}`，挂到普通餐品 item 上无效；券不抵扣已有餐品金额
- **菜单名 ≠ 详情名**（如「麦咖啡™美式」vs「麦咖啡™冰美式中杯」）→ 跨工具一律按 **code** 关联
- **`list-nutrition-foods`** 返回文本表 9 列，第 2 列 `nutritionDescription` 必须剔除，否则菜名被污染
- **`query-nearby-stores`** 单次恒返回 5 家、无分页；经纬度恒为 `0.0`

## 速率与合规

- 批量拆解/核价保持温和限速（脚本内置 0.12s 间隔），远低于 600 次/分钟上限
- 不输出真实 Token / 个人信息；配置文件仅用环境变量占位符
- 餐品、价格、券与供应状态一律以官方实时结果为准
- 完全只读，绝不代用户下单或操作支付
- 营养基准全部取自麦当劳官方营养数据，不引入外部标准，不构成营养/医疗建议
