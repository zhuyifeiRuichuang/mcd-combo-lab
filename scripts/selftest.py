#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：不联网、不需要 Token，校验解析与渲染的关键不变量。

运行：python scripts/selftest.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_card import render_card, yuan  # noqa: E402
from combo_lab import (  # noqa: E402
    EXAMPLES, ROOT, SEED_PATH, aggregate_nutrition, build_nutrition_index,
    current_daypart, load_recipe, match_nutrition, normalize_name,
    nutrition_percentile, parse_nutrition_text, parse_time_windows,
    price_recipe_offline, verify_recipe,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, (" -> " + detail) if detail else ""))


def main():
    print("== 1. 营养表解析（第 2 列 nutritionDescription 必须剔除） ==")
    sample = (
        "[160]{productName,nutritionDescription,energyKj,energyKcal,protein,"
        "fat,carbohydrate,sodium,calcium}:\n"
        "  猪柳麦满分,null,1288,308,16,16,24,781,213\n"
        "  可乐中杯,null,540,129,0,0,32,12,0\n"
    )
    rows = parse_nutrition_text(sample)
    check("解析出 2 行", len(rows) == 2, str(len(rows)))
    check("菜名未被 null 污染", rows and rows[0]["productName"] == "猪柳麦满分",
          rows[0]["productName"] if rows else "")
    check("热量正确 (energyKcal 列)", rows and rows[0]["kcal"] == 308.0)
    check("钠正确", rows and rows[0]["sodium"] == 781.0)
    check("可乐菜名正确", rows and rows[1]["productName"] == "可乐中杯",
          rows[1]["productName"] if len(rows) > 1 else "")

    print("\n== 2. 菜名归一化与匹配 ==")
    check("全角括号归一化", normalize_name("麦咖啡™冰美式（中杯）") == "麦咖啡冰美式(中杯)",
          normalize_name("麦咖啡™冰美式（中杯）"))
    idx = build_nutrition_index(rows)
    check("原名精确匹配", match_nutrition("猪柳麦满分", idx) is not None)
    check("无关名不匹配", match_nutrition("完全不存在的菜", idx) is None)

    print("\n== 3. 餐段窗口解析 ==")
    w = parse_time_windows("宵夜(00:00至04:45,22:14至23:59)，早餐(05:14至10:15)")
    check("解析出 3 段（多区间餐段不丢）", len(w) == 3, str(len(w)))
    check("宵夜第一段 0-285", w[0] == ("宵夜", 0, 285), str(w[0]))
    check("宵夜第二段 1334-1439", w[1] == ("宵夜", 22 * 60 + 14, 23 * 60 + 59), str(w[1]))
    check("早餐 314-615", w[2] == ("早餐", 314, 615), str(w[2]))
    check("跨零点餐段可命中",
          current_daypart({"reservationTimeOptions": [
              {"today": True,
               "reservationOptionText": "宵夜(00:00至04:45,22:14至23:59)，午餐(10:44至14:15)"}]},
              23 * 60 + 30) == "宵夜")
    store = {"reservationTimeOptions": [
        {"today": True, "reservationOptionText": "午餐(10:44至14:15)，下午茶(14:44至16:45)"},
        {"today": False, "reservationOptionText": "早餐(05:14至10:15)"}]}
    check("13:00 判定为午餐", current_daypart(store, 13 * 60) == "午餐",
          current_daypart(store, 13 * 60))
    check("忽略非今天的时段", current_daypart(store, 6 * 60) == "",
          repr(current_daypart(store, 6 * 60)))

    print("\n== 4. 金额换算（分 -> 元） ==")
    check("3450 -> 34.50", yuan(3450) == "34.50")
    check("None -> '-'", yuan(None) == "-")

    print("\n== 5. 配方种子库 ==")
    with open(SEED_PATH, encoding="utf-8") as fh:
        seeds = json.load(fh)
    recipes = seeds.get("recipes") or []
    check("种子配方 >= 3 条", len(recipes) >= 3, str(len(recipes)))
    check("每条都有 id/name/items", all(r.get("id") and r.get("name") and r.get("items")
                                        for r in recipes))
    check("配方不含硬编码价格字段", all("price" not in r for r in recipes))
    check("load_recipe 按 id 可取", load_recipe(recipes[0]["id"])["id"] == recipes[0]["id"])

    print("\n== 6. 离线样例包（真实抓取，非伪造） ==")
    bundle_path = os.path.join(EXAMPLES, "demo_bundle.json")
    if not os.path.isfile(bundle_path):
        print("  [SKIP] 缺少 demo_bundle.json")
        return finish()
    with open(bundle_path, encoding="utf-8") as fh:
        bundle = json.load(fh)
    check("含门店", bool(bundle.get("store", {}).get("storeCode")))
    check("含菜单", len((bundle.get("menu") or {}).get("meals") or {}) > 0)
    check("含营养表", len(bundle.get("nutrition") or []) > 0)
    check("含券", isinstance(bundle.get("coupons"), list))

    print("\n== 7. 离线复核 / 核价 / 营养分位 ==")
    menu = bundle["menu"]
    details = bundle["details"]
    nidx = build_nutrition_index(bundle["nutrition"])
    for r in recipes:
        v = verify_recipe({}, bundle["store"], r, menu, details, nidx, bundle["now_minutes"])
        check("verify[%s] 组件齐全" % r["id"], len(v["components"]) == len(r["items"]))
        check("verify[%s] 全部可售" % r["id"], v["all_on_sale"])
        p = price_recipe_offline(bundle, r, bundle.get("coupons") or [])
        check("price[%s] 有原价与实付" % r["id"],
              bool(p.get("base_original_yuan")) and bool(p.get("base_yuan")))
        check("price[%s] 券加购总价 >= 实付" % r["id"],
              all((t.get("total_with_addon_cents") or 0) >= int(float(p["base_yuan"]) * 100)
                  for t in p.get("coupon_trials") or [] if "error" not in t))
        n = aggregate_nutrition(r, details, nidx)
        check("nutrition[%s] 热量 > 0" % r["id"], n["total"]["kcal"] > 0)
        pct = nutrition_percentile(menu, details, nidx, n["total"]["kcal"])
        check("percentile[%s] 样本 > 0" % r["id"], (pct.get("sample") or 0) > 0)

    print("\n== 8. 卡片渲染 ==")
    r0 = recipes[0]
    payload = {
        "verify": verify_recipe({}, bundle["store"], r0, menu, details, nidx, bundle["now_minutes"]),
        "price": price_recipe_offline(bundle, r0, bundle.get("coupons") or []),
        "nutrition": aggregate_nutrition(r0, details, nidx),
        "percentile": nutrition_percentile(menu, details, nidx, 100),
        "generated_at": "2026-10-12T00:00:00", "offline": True,
    }
    out = os.path.join(EXAMPLES, "_selftest_card.html")
    render_card(payload, out)
    with open(out, encoding="utf-8") as fh:
        html = fh.read()
    check("HTML 已生成", len(html) > 2000)
    check("无未替换占位符", "__TITLE__" not in html and "__BASE_YUAN__" not in html)
    check("含非官方声明", "非麦当劳官方产品" in html)
    check("含券加购说明", "不抵扣已有餐品金额" in html)
    check("无外链资源", "<script" not in html and "http://" not in html.replace("http://www.w3.org", ""))
    os.remove(out)

    print("\n== 9. 安全：仓库内不得出现真实 Token ==")
    # 只判定「凭证值」：环境变量名 MCD_MCP_TOKEN 本身是合法占位符，不算泄露。
    # 真实 Token 为 32 位以上字母数字，凡出现 MCD_MCP_TOKEN=<长串> 一律视为泄露。
    token_re = re.compile(r"MCD_MCP_TOKEN\s*=\s*[\"']?[A-Za-z0-9_\-]{16,}")
    bearer_re = re.compile(r"Bearer\s+[A-Za-z0-9_\-]{16,}")
    bad = []
    for base, _dirs, files in os.walk(ROOT):
        if ".git" in base or "__pycache__" in base:
            continue
        for f in files:
            if not f.endswith((".py", ".md", ".json", ".html")):
                continue
            p = os.path.join(base, f)
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except OSError:
                continue
            if token_re.search(txt) or bearer_re.search(txt):
                bad.append(p)
    check("无脚本/文档硬编码 Token 值", not bad, ",".join(bad))
    cfg = os.path.join(ROOT, "mcp-config.example.json")
    with open(cfg, encoding="utf-8") as fh:
        cfg_txt = fh.read()
    check("配置示例仅用占位符", "${MCD_MCP_TOKEN}" in cfg_txt
          and not re.search(r"[A-Za-z0-9_\-]{32,}", cfg_txt.replace("${MCD_MCP_TOKEN}", "")))

    return finish()


def finish():
    print("\n==== 自检结果：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  失败项：%s" % f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
