#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单文件 HTML 配方卡渲染器（内联 CSS，无外部依赖）。

注意：模板里含大量 CSS 百分号（width:100%），一律用 __PLACEHOLDER__ + str.replace
渲染，绝不使用 % 格式化，否则会被误食。
"""

import json
import os

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --line:#e3e6ea; --muted:#6b7280; --bg:#ffffff; --soft:#f7f8fa;
          --ok:#0a7d3c; --ng:#b42318; --accent:#c8102e; --chip:#eef1f5; }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:#1f2328;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
         line-height:1.6; }
  .wrap { max-width:860px; margin:0 auto; }
  h1 { font-size:24px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  h2 { font-size:16px; margin:26px 0 10px; padding-left:9px; border-left:3px solid var(--accent); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; }
  th { background:var(--soft); font-weight:600; white-space:nowrap; }
  .tag { display:inline-block; padding:1px 7px; border-radius:10px; font-size:12px;
         background:var(--chip); color:#374151; margin:2px 4px 2px 0; }
  .ok { color:var(--ok); font-weight:600; }
  .ng { color:var(--ng); font-weight:600; }
  .price { font-size:26px; font-weight:700; color:var(--accent); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
  .cell { border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:var(--soft); }
  .cell .k { font-size:12px; color:var(--muted); }
  .cell .v { font-size:18px; font-weight:700; }
  .note { background:#fff8e6; border:1px solid #f2d98d; border-radius:8px;
          padding:10px 12px; font-size:13px; color:#6b4c00; margin-top:10px; }
  .foot { margin-top:28px; padding-top:14px; border-top:1px solid var(--line);
          font-size:12px; color:var(--muted); }
  .muted { color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<div class="wrap">

  <h1>__TITLE__</h1>
  <div class="sub">__STORY__</div>

  <h2>可点性</h2>
  <div class="grid">
    <div class="cell"><div class="k">门店</div><div class="v" style="font-size:14px">__STORE_NAME__</div></div>
    <div class="cell"><div class="k">当前餐段</div><div class="v" style="font-size:14px">__DAYPART__</div></div>
    <div class="cell"><div class="k">营业时间</div><div class="v" style="font-size:14px">__HOURS__</div></div>
    <div class="cell"><div class="k">整体可售</div><div class="v" style="font-size:14px">__ALL_ON_SALE__</div></div>
  </div>
  <div class="muted" style="margin-top:6px">门店编码 __STORE_CODE__ · __ADDRESS__</div>

  <h2>组成清单</h2>
  __COMPONENTS_TABLE__

  <h2>官方核价</h2>
  <div class="grid">
    <div class="cell"><div class="k">官方原价</div><div class="v">¥__BASE_ORIGINAL_YUAN__</div></div>
    <div class="cell"><div class="k">实付（含官方自动优惠）</div><div class="v price">¥__BASE_YUAN__</div></div>
    <div class="cell"><div class="k">自动优惠</div><div class="v" style="font-size:14px">__BASE_DISCOUNT__</div></div>
    __PRICE_CELLS__
  </div>
  __COUPON_TABLE__
  <div class="note">__COUPON_NOTE__</div>

  <h2>营养与份量</h2>
  <div class="grid">
    __NUTRITION_CELLS__
  </div>
  <div class="muted" style="margin-top:6px">__NUTRITION_NOTE__</div>
  <div class="note">__PORTION_NOTE__</div>

  <div class="foot">
    __FOOTER__
  </div>

</div>
</body>
</html>
"""


def esc(text):
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def yuan(cents):
    try:
        return "%.2f" % (int(cents) / 100.0)
    except (TypeError, ValueError):
        return "-"


def render_components(verify):
    rows = []
    for c in verify.get("components") or []:
        status = ('<span class="ok">可售</span>' if c.get("on_sale")
                  else '<span class="ng">不可售</span>')
        subs = "".join('<span class="tag">%s %s</span>' % (esc(s.get("name")), esc(s.get("diffPrice")))
                       for s in (c.get("substitutes") or [])[:8]) or '<span class="muted">无可替换项</span>'
        mods = "".join('<span class="tag">%s +¥%s</span>' % (esc(m.get("name")), yuan(m.get("price_cents")))
                       for m in (c.get("mods") or [])[:6]) or '<span class="muted">无加价特制</span>'
        rows.append(
            "<tr><td>%s<br><span class='muted'>code %s</span></td><td>%s</td><td>¥%s</td>"
            "<td>%s<br><span class='muted'>%s</span></td><td>%s</td><td>%s</td></tr>" % (
                esc(c.get("menu_name") or c.get("productCode")), esc(c.get("productCode")),
                esc(c.get("quantity")), esc(c.get("menu_price_yuan") or "-"),
                status, esc(c.get("advice") or ""), subs, mods))
    return ("<table><thead><tr><th>餐品</th><th>数量</th><th>菜单当前价</th><th>状态</th>"
            "<th>可替换项（官方差价）</th><th>加价特制</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def render_coupons(price):
    trials = [t for t in (price.get("coupon_trials") or []) if "error" not in t]
    if not trials:
        return ("<div class='muted'>本门店当前无可用券，或券查询已降级。"
                "下方价格即配方原价，无任何抵扣。</div>")
    rows = []
    for t in trials:
        rows.append(
            "<tr><td>%s<br><span class='muted'>%s</span></td><td>¥%s</td><td>¥%s</td>"
            "<td class='ok'>省 ¥%s</td><td>¥%s</td><td>¥%s</td></tr>" % (
                esc(t.get("title")), esc(t.get("validity") or ""),
                yuan(t.get("addon_original_cents")), yuan(t.get("addon_pay_cents")),
                yuan(t.get("addon_saved_cents")), yuan(t.get("total_with_addon_cents")),
                yuan(t.get("total_discount_cents"))))
    return ("<table><thead><tr><th>加购券商品</th><th>券商品原价</th><th>用券价</th>"
            "<th>券省</th><th>配方+加购总价</th><th>合计省</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def render_card(payload, out_path):
    verify = payload.get("verify") or {}
    price = payload.get("price") or {}
    nut = (payload.get("nutrition") or {}).get("total") or {}
    agg = payload.get("nutrition") or {}
    pct = payload.get("percentile") or {}
    store = verify.get("store") or {}
    recipe = verify.get("recipe") or {}

    # 份量提示（与 mcd-just-enough「控量」互补：本项目只负责核价与可点性）
    pieces = agg.get("pieces") or 0
    portion = ("本配方共 %d 件（按餐品与套餐默认子项计）。" % pieces)
    if pieces >= 4:
        portion += " 件数偏多，注意份量——「吃多少」请参考反浪费类工具，本项目只回答「能不能点、多少钱」。"
    elif pieces >= 2:
        portion += " 属于组合搭配，按需取用。"
    else:
        portion += " 单件配方。"

    unknown = agg.get("unknown_items") or []
    nut_note = ("营养按配方真实子项聚合（套餐取其默认子项）。已匹配 %d 项" % agg.get("known_items", 0))
    if unknown:
        nut_note += "，未匹配到官方营养数据的有 %s —— 合计为已知部分之和，非整份真实合计。" % "、".join(unknown[:4])
    else:
        nut_note += "，全部子项均已匹配官方营养数据。"
    if pct.get("percentile") is not None:
        nut_note += (" 热量高于本店 %d%% 的在售餐品（参照样本 %d 款，中位数 %s kcal）。"
                     % (pct["percentile"], pct.get("sample") or 0, pct.get("median")))
    nut_note += " 基准全部取自麦当劳官方营养数据，不引入任何外部膳食标准。"

    cells = ""
    for label, key, unit in (("热量", "kcal", "kcal"), ("钠", "sodium", "mg"),
                             ("蛋白质", "protein", "g"), ("脂肪", "fat", "g"),
                             ("碳水", "carbohydrate", "g")):
        cells += ("<div class='cell'><div class='k'>%s（%s）</div><div class='v'>%s</div></div>"
                  % (label, unit, esc(nut.get(key))))

    price_cells = ""
    best = price.get("best_addon")
    if best:
        price_cells = (
            "<div class='cell'><div class='k'>加购省得最多</div>"
            "<div class='v' style='font-size:14px'>%s</div>"
            "<div class='k'>券自身省 ¥%s</div></div>"
            "<div class='cell'><div class='k'>配方 + 该加购件</div>"
            "<div class='v'>¥%s</div></div>" % (
                esc(best.get("title")), yuan(best.get("addon_saved_cents")),
                yuan(best.get("total_with_addon_cents"))))
    else:
        price_cells = ("<div class='cell'><div class='k'>可加购券</div>"
                       "<div class='v' style='font-size:14px'>无</div></div>")

    html = TEMPLATE
    repl = {
        "__TITLE__": esc(recipe.get("name") or "未命名配方"),
        "__STORY__": esc(recipe.get("story") or ""),
        "__STORE_NAME__": esc(store.get("storeName")),
        "__STORE_CODE__": esc(store.get("storeCode")),
        "__ADDRESS__": esc(store.get("address")),
        "__DAYPART__": esc(verify.get("daypart") or "未知"),
        "__HOURS__": esc(store.get("hours")),
        "__ALL_ON_SALE__": ('<span class="ok">全部可售</span>' if verify.get("all_on_sale")
                            else '<span class="ng">存在不可售组件</span>'),
        "__COMPONENTS_TABLE__": render_components(verify),
        "__BASE_YUAN__": esc(price.get("base_yuan") or "-"),
        "__BASE_ORIGINAL_YUAN__": esc(price.get("base_original_yuan") or "-"),
        "__BASE_DISCOUNT__": ("省 ¥%s" % esc(price.get("base_discount_yuan"))
                              if price.get("base_has_auto_discount")
                              else '<span class="muted">无自动优惠</span>'),
        "__PRICE_CELLS__": price_cells,
        "__COUPON_TABLE__": render_coupons(price),
        "__COUPON_NOTE__": ("麦当劳门店券是<strong>独立加购商品</strong>，不是折扣券："
                            "它不抵扣已有餐品金额，只会往购物车多加一份用券商品。"
                            "因此「配方 + 加购件」的总价必然 ≥ 配方原价。"
                            "若你不想加购任何东西，实付就是上面的配方原价。"),
        "__NUTRITION_CELLS__": cells,
        "__NUTRITION_NOTE__": esc(nut_note),
        "__PORTION_NOTE__": esc(portion),
        "__FOOTER__": ("本项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，"
                       "<strong>非麦当劳官方产品</strong>。所有餐品、价格、券与供应状态均来自"
                       "麦当劳官方 MCP 实时返回；输出仅供参考，不构成医疗、营养或其他专业建议，"
                       "餐品信息与价格以麦当劳官方渠道实时结果为准。本项目<strong>不代下单</strong>，"
                       "链路终点为官方核价。生成时间 %s%s。" % (
                           esc(payload.get("generated_at")),
                           "（离线样例数据）" if payload.get("offline") else "")),
    }
    for key, val in repl.items():
        html = html.replace(key, val)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("用法：build_card.py <payload.json> <out.html>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    print(render_card(data, sys.argv[2]))
