# 麦麦组合实验室 / `mcd-combo-lab`

> 麦当劳程序员创意开发大赛参赛作品 · 版本 v1.0.0

用麦当劳官方 MCP 校验一件很具体的事：**你自创的那些非常规搭配，在官方系统里到底能不能点、到底多少钱。**

## 一、先说清楚一件事：官方没有「隐藏菜单」

网络上流传的「六层吉士堡」「加双层牛肉饼」，**在麦当劳官方 MCP 里核不出价**。这不是本项目的疏漏，而是实测结论：

- 全店 49 个在售单品逐个拆解后，汉堡类的可定制项**全是二值开关**——吉士 / 酸黄瓜 / 洋葱粒 / 番茄酱 / 芥末酱，特征为 `price=0`、`maxQuantity=1`、带 `unselectedKey`。只能「要不要」，不能「加几份」。
- 菜单里**根本没有**「牛肉饼」「吉士片」出售。
- 全店唯一会真实加价的特制项在咖啡上：麦咖啡「加」浓缩，`price=500`（¥5）。

所以本项目**不假装支持加料**，只做官方真实能核价的三类操作：

| 类型 | 说明 | 实测证据 |
|---|---|---|
| **套餐子项替换** | 换大薯条、换奶昔、换饮料……`diffPrice` 明确标价 | 三件套换大薯条 ¥37.50 → ¥40.00，与官方 `+ ¥2.5` 完全吻合 |
| **加价特制** | 少数餐品提供的加价定制 | 麦咖啡加浓缩 ¥19.00 → ¥24.00 |
| **按件叠搭** | 想要「六层」，官方可核价的真实做法就是点 N 件自己叠 | 双层吉士汉堡 ×3 = ¥69.00 |

减法特制（去酸黄瓜、去酱）真实可调，但**不改价**——项目如实标注，不拿来充数。

## 二、目标用户

- 爱折腾 DIY 搭配、想晒组合的麦门创意党
- 想知道「这样搭到底多少钱」而不是「大概多少钱」的理性消费者
- 想确认「这个组合现在这个时段还点不点得到」的人

## 三、安装与配置

1. 申请麦当劳中国 MCP Token：<https://open.mcd.cn/mcp>
2. 在 MCP Client 中配置 `mcd-mcp`（参考 `mcp-config.example.json`，Token 用环境变量 `MCD_MCP_TOKEN` 占位，**禁止硬编码**）
3. 无 Token 也能跑：内置真实抓取的离线样例，`python scripts/combo_lab.py demo` 即可完整演示

```bash
export MCD_MCP_TOKEN=你的Token

# 1) 门店（收藏优先，为空自动回退位置搜索）
python scripts/combo_lab.py stores --city 上海 --keyword 人民广场

# 2) 拆一个餐品，看它真实能改什么
python scripts/combo_lab.py inspect --store-code 1450713 --code 1120

# 3) 复核配方（在售性 / 当前餐段 / 可替换项）
python scripts/combo_lab.py verify --store-code 1450713 --recipe seed-combo-upgrade

# 4) 官方核价（原价 / 实付 / 加购券三档）
python scripts/combo_lab.py price  --store-code 1450713 --recipe seed-caffeine-combo

# 5) 生成单文件 HTML 配方卡
python scripts/combo_lab.py card   --store-code 1450713 --recipe seed-caffeine-combo

# 6) 离线演示（无需 Token）
python scripts/combo_lab.py demo

# 7) 自检（离线，50 项）
python scripts/selftest.py
```

## 四、使用示例

**用户**：我想叠一个六层吉士堡，帮我核价看真实能不能点

**助手**（真实输出节选）：

```
配方：双层吉士汉堡 ×3 叠搭
官方原价：¥69.00
实付（含官方自动优惠）：¥69.00
```

并明确告知：官方不提供加料，「六层」只能通过点 3 个双层吉士汉堡实现；这是官方可核价的真实做法，不是隐藏菜单。

**用户**：那把三件套的薯条升大杯呢

```
官方原价：¥40.00
实付（含官方自动优惠）：¥40.00
```

配方卡同时列出该套餐**所有**可替换项及其官方差价（大薯条 +¥2.5、月下相思扭扭薯 +¥1.5、小杯玉米杯 -¥0.5 …）。

## 五、核价口径（本项目最较真的地方）

`calculate-price` 会返回两个不同的数，**必须分开报**：

| 字段 | 含义 | 实例 |
|---|---|---|
| `originalPrice` | 官方原价 | ¥47.00 |
| `price` | 实付 | ¥39.90 |
| `discount` | **非券**的官方自动优惠 | ¥7.10 |

实测麦咖啡存在促销活动（¥19 → ¥11.9），加浓缩后 ¥24 → ¥16.9，`discount` 如实带出 ¥7.10。只报一个数必然报错。

### 券：是加购商品，不是折扣券

这一点极其反直觉，也是最容易写错的地方。实测四种组装方式：

| 方式 | 结果 |
|---|---|
| 只核券商品、不带券 ID | **0 分**（券商品本身无价） |
| 券商品 + `couponId`/`couponCode` | ¥27.00 → ¥13.50（买一送一） |
| 配方 + 券商品带券 | 总价 = 配方 + 券商品用券价 |
| `couponId` 挂到普通餐品上 | **返回 None，无效** |

结论：券**不抵扣**已有餐品金额，加购后总价必然 ≥ 配方原价。本项目的卡片会明确写出这句话，绝不表述为「打几折」。

另外，三个券工具里**只有 `query-store-coupons` 返回结构化 `couponId` 可进核价**；`available-coupons` 与 `query-my-coupons` 均返回 Markdown 且无 `couponId`，只能展示。

## 六、使用的麦当劳 MCP 工具

| 工具 | 用途 |
|---|---|
| `query-nearby-stores` | 双路获取门店（收藏优先，空则位置搜索） |
| `query-meals` | 拉取门店当前在售菜单，判定组件可售性 |
| `query-meal-detail` | 拆解餐品真实组成、可替换项与特制项 |
| `calculate-price` | 官方核价（原价 / 实付 / 自动优惠 / 用券） |
| `query-store-coupons` | 门店可用券（唯一带 `couponId` 的券源） |
| `list-nutrition-foods` | 官方营养数据，用于真实子项聚合与分位基准 |
| `now-time-info` | 取麦当劳服务器时间判定当前餐段 |

**不调用 `create-order`**：本项目完全只读，链路终点是官方核价。

## 七、营养与份量的口径

- 营养按配方**真实子项**聚合（套餐取其 `rounds` 默认子项），不是按套餐名猜
- 部分子项匹配不到官方营养数据时，如实标注「已知 X 项，未匹配 Y 项」，不假装是整份真实合计
- 高低判断的基准**全部来自麦当劳自家数据**（本店已匹配营养的在售餐品分布），**不引入任何外部膳食标准**——因此不会出现「超出每日建议摄入量」这类无从追溯的结论
- 份量提示：本配方共 N 件。关于「吃多少」请参考反浪费类工具（如 `mcd-just-enough`），本项目只回答「能不能点、多少钱」

## 八、项目结构

```
mcd-combo-lab/
├── SKILL.md                    工作流定义
├── MCP_INTEGRATION.md          MCP 接入说明
├── CONTEST_DECLARATION.md      参赛声明（官方文件，内容不可改）
├── mcp-config.example.json     配置示例（仅环境变量占位符）
├── workbuddy.md                WorkBuddy 开发上下文
├── scripts/
│   ├── mcd_mcp_client.py       底层 MCP 客户端（纯标准库）
│   ├── combo_lab.py            业务引擎（stores/menu/inspect/verify/price/card/demo）
│   ├── build_card.py           单文件 HTML 配方卡渲染
│   └── selftest.py             离线自检（50 项）
├── examples/
│   ├── seed_recipes.json       配方种子库（只存 code，不存价格）
│   ├── demo_bundle.json        真实抓取的离线样例包
│   └── demo_card_*.html        配方卡示例
└── references/
    ├── mcp-tools.md            35 个工具（live tools/list 程序化生成）
    └── live-contract.md        字段级契约（代码唯一事实来源）
```

## 九、合规要点

- 配方必须基于真实在售餐品，不虚构可点性
- 价格一律来自官方 `calculate-price`，不估算、不插值
- 完全只读，不代下单
- 不跨品牌对比；营养输出不构成医疗或营养建议
- Token 仅通过环境变量注入，仓库内无任何凭证

## 十、已知限制

- `query-nearby-stores` 单次恒返回 5 家、无分页；经纬度恒为 `0.0`，无法渲染真实地图
- 菜单随时段变化，配方会过期——因此配方库只存 `productCode`，每次加载强制用官方数据复核并标注「仍可点 / 已失效 / 价格已变动」
- 加料能力官方不提供，本项目不做

## 声明

参赛项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，非麦当劳官方产品。输出仅供参考，餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。
