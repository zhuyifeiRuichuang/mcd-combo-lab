# 麦当劳 MCP live 字段级契约

> 全部结论来自 2026-10-12 对 `https://mcp.mcd.cn` 的真实调用（门店 `1450713` 麦当劳上海黄浦华旭国际大厦餐厅）。
> **本文件是本项目代码的唯一事实来源，改代码前先看这里，勿凭记忆或官方文档示例修改。**

## 0. 协议层

| 项 | 实测结论 |
|---|---|
| 鉴权 | 单头 `Authorization: Bearer <TOKEN>` 即通；本项目用到的 6 个工具均未要求 `authToken` 头 |
| Token 失效 | **HTTP 401 + 纯文本响应体**（非 JSON），必须单独分支，不要做 JSON 解析 |
| `isError` | **恒为 `false`**。业务失败藏在 `structuredContent.success=false` + `code`/`message`，只看 `isError` 会把失败当成功 |
| 会话 | 服务端无状态，`initialize` 不返回 `mcp-session-id` |
| 限流 | 单 Token 600 次/分钟 |

优先读 `structuredContent`（规整 `{success, code, message, datetime, traceId, data}`），文本解析仅作回退。

## 1. query-nearby-stores

入参：`beType`（1 到店自提 / 5 得来速）、`searchType`（1 收藏 / 2 按位置）、`city`、`keyword`（`searchType=2` 时必填）。

- **单次恒返回 5 家，无分页参数** → 覆盖度只能靠多关键词扇出 + `storeCode` 去重
- **`longitude` / `latitude` 恒为 `0.0`** → 画不出真实地图，只能使用 `distance`
- `beType=1` 返回的门店**无 `beCode`**；`beType=5` 有
- 返回字段：`storeCode / storeName / address / distance / businessStatus / businessStartTime / businessEndTime / reservation / reservationTimeOptions`
- 错误码：`600014` 未查到地址；`600058` 城市或关键词为空；**`600050` 收藏餐厅列表为空**（本项目据此回退位置搜索）

### reservationTimeOptions（餐段判定黄金数据源）

```json
{"date":"2026-10-12","today":true,
 "reservationOptionText":"午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)，宵夜(22:14至23:59)"}
```

- 段间分隔符是全角 **`，`**；同一段多个区间用半角 **`,`**（如 `宵夜(00:00至04:45,22:14至23:59)`）
- 今日已过时段不再返回
- 只取 `today=true` 的那条

## 2. query-meals

入参：`storeCode`、`orderType`、`beType`（到店自取不传 `beCode`）。

- 返回 `{categories, meals}`；`meals` 是 `code -> {name, currentPrice, originalPrice, ...}` 的映射
- **价格单位是元（字符串）**，如 `"23"`
- **live 不返回 `daypart`、餐品不返回 `tags`**（官方文档示例有这两个字段，已过时）
- 菜单随时段动态变化
- **菜单名 ≠ 详情名**：例如 `507411` 菜单叫「麦咖啡™美式」，`query-meal-detail` 叫「麦咖啡™冰美式中杯」→ 跨工具一律按 **code** 关联

## 3. query-meal-detail

入参：`storeCode`、`orderType`、`beType`、`code`。

```json
{"code":"1120","name":"双层吉士汉堡","rounds":[],
 "modification":{"items":[{"maxValues":5,"minValues":0,"values":[
   {"code":"100141","price":0,"name":"吉士","maxQuantity":1,
    "selectedQuantity":1,"selectedKey":"0-1","unselectedKey":"0-0"}]}]},
 "supportModify":true}
```

**关键结论（本项目立足点）：不存在加料能力。**

- 汉堡类的可定制项全是**二值开关**：吉士 / 酸黄瓜 / 洋葱粒 / 番茄酱 / 芥末酱，特征为
  `price=0`、`maxQuantity=1`、带 `unselectedKey`（只能「要不要」，不能「加几份」）
- 全店 49 个单品扫完，**唯一 `price>0` 的特制项在咖啡上**：`code 8008`「加」`price=500`（¥5）
- 菜单里**没有**「牛肉饼」「吉士片」在售 → 「六层吉士堡」这类加料配方**核不出价**
- `rounds[].choices[]` 是套餐真实组成：`isDefault` 标默认项、`diffPrice` 是替换差价（如 `"+ ¥2.5"`）

## 4. calculate-price

入参：`storeCode`、`orderType`、`beType`、`items[]`（`productCode` / `quantity` / `roundList` / `modification` / `couponId` / `couponCode`）。

返回体（**全部单位为分**）：

```json
{"productOriginalPrice":4700,"productPrice":3990,"originalPrice":4700,
 "discount":710,"price":3990,
 "productList":[{"productCode":"507411","productName":"麦咖啡™冰美式中杯",
                 "quantity":1,"originalSubtotal":2400,"subtotal":1690}],
 "takeWayList":[{"code":"eat-in","title":"堂食","subtitle":"店内用餐"}]}
```

**实测三类定制的核价响应：**

| 定制类型 | 案例 | 结果 |
|---|---|---|
| 加价特制 | 麦咖啡「加」浓缩（`code 8008`, `key 0-1`, `price=500`） | ¥19.00 → ¥24.00 **真实进价** |
| 套餐子项替换 | 三件套换大薯条（`roundList.round=2`, `code 4820`） | ¥37.50 → ¥40.00，与 `diffPrice "+ ¥2.5"` 完全吻合 |
| 减法特制 | 双层吉士汉堡去掉吉士 | ¥23.00 → ¥23.00 **不改价** |

**必须分开报两个数：**

- `originalPrice` = 官方原价
- `price` = 实付
- `discount` = **非券的官方自动优惠**（实测咖啡促销 ¥19→¥11.9，加浓缩后 ¥24→¥16.9，discount=710）

即「实付」可能低于原价，且**与券无关**。只报一个数必然报错。

## 5. 券（反直觉，最容易写错）

三个券工具的能力完全不同：

| 工具 | 返回 | 能否进核价 |
|---|---|---|
| `query-store-coupons` | **结构化**：`title / couponId / couponCode / products[{productCode,productName}] / tradeDateTime / promotionId` | ✅ 唯一可核价的券源 |
| `available-coupons` | Markdown（标题 + 状态 + 图片），**无 couponId** | ❌ |
| `query-my-coupons` | Markdown（含「优惠: ¥13.5 (用券价格)」+ 有效期 + 标签），**无 couponId** | ❌ |

`query-store-coupons` 必带 `orderType` + `beType`，缺失返回 `success:false code:400`。

**券是「独立加购商品」，不是折扣券。** 实测四种组装方式：

| 方式 | 结果 |
|---|---|
| 只核券商品、不带券 ID | **0 分**（券商品本身无价） |
| 券商品 + `couponId`/`couponCode` | ¥27.00 → ¥13.50（买一送一），`discount=1350` |
| 三件套 + 券商品带券 | ¥64.50 → ¥51.00，`discount=1350` |
| `couponId` 挂到普通餐品 item 上 | **返回 None，无效** |

正确用法：把券作为**一整个 item** 追加进 `items`：

```json
{"productCode":"9900012387","quantity":1,
 "couponId":"8692944B045928451618BFF22F535D2A","couponCode":"MCD697730490QN1810108"}
```

语义结论：券**不抵扣**已有餐品金额，加购后总价必然 ≥ 配方原价。对外绝不能表述为「打几折」。

## 6. list-nutrition-foods

返回**文本表 9 列**：`productName, nutritionDescription, energyKj, energyKcal, protein, fat, carbohydrate, sodium, calcium`。

- **第 2 列 `nutritionDescription` 必须显式剔除**，否则菜名会被污染成「可乐中杯,null」，按名匹配全线失败
- 实测 160 行全部为标准 9 列

## 7. now-time-info

`data{timestamp, formatted, timezone:"GMT+08:00", ...}`。本项目用它取**麦当劳服务器时间**判定餐段，不依赖本地时钟。
