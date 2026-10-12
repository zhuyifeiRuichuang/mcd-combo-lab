# 变更日志

## v1.0.0（2026-10-12）

首个正式版本。全部能力基于麦当劳 MCP live 实测，未使用任何推测或过时配置。

### 新增

- `scripts/mcd_mcp_client.py`：底层 MCP Streamable HTTP 客户端（纯标准库，零第三方依赖）
- `scripts/combo_lab.py`：业务引擎，9 个子命令
  - `stores` 双路获取门店（收藏优先，为空回退位置搜索）
  - `menu` 拉在售菜单 + 官方营养表
  - `inspect` 拆解餐品真实可定制空间（加价特制 / 免费开关 / 套餐子项替换）
  - `verify` 复核配方（在售性 + 当前餐段 + 可替换项）
  - `price` 官方核价（原价 / 实付 / 自动优惠 / 加购券三档）
  - `save` 追加配方到种子库
  - `card` 渲染单文件 HTML 配方卡
  - `bundle` 抓取离线样例包
  - `demo` 离线全流程（无需 Token）
- `scripts/build_card.py`：单文件 HTML 配方卡渲染（内联 CSS，无外部依赖）
- `scripts/selftest.py`：离线自检，50 项全部通过
- `examples/seed_recipes.json`：3 条真实配方种子（只存 code，不存价格）
- `examples/demo_bundle.json`：live 抓取的真实样例包
- `references/mcp-tools.md`：35 个工具，由 live `tools/list` 程序化生成
- `references/live-contract.md`：字段级契约，代码唯一事实来源

### 关键设计决策

- **诚实降级**：官方 MCP 不提供加料能力（汉堡可定制项全是二值开关），项目不假装支持，改为做官方真实可核价的组合 / 替换 / 叠搭
- **原价与实付分开报**：`calculate-price` 的 `discount` 会带出非券自动优惠
- **券=加购组件**：券不抵扣已有餐品金额，加购后总价必然 ≥ 配方原价
- **营养基准只用麦当劳自家数据**，不引入外部膳食标准
- **完全只读**，不接 `create-order`

### 修复

- `parse_time_windows` 多区间餐段被半角逗号切碎导致丢段
- `demo` 子命令 `args.out` 迭代间未重置，JSON 覆盖写进上一轮 HTML 卡片
- 自检安全检查把环境变量名误判为凭证泄露
