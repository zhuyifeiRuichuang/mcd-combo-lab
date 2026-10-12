#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦组合实验室 / mcd-combo-lab —— 官方可核价的非常规搭配引擎。

设计前提（2026-10-12 live 实测，勿凭记忆改动）：
  * 麦当劳官方 MCP **不提供加料能力**。汉堡类 modification 全是二值开关
    （吉士 / 酸黄瓜 / 洋葱粒 / 番茄酱 / 芥末酱，price=0、maxQuantity=1）。
    因此「六层吉士堡」这类加料配方**核不出价**，本项目不做、也不假装有。
  * 真正能被官方核价响应的定制只有两类：
      1) 加价特制（modification.values 里 price>0 的项，实测咖啡「加」浓缩 +¥5）
      2) 套餐子项替换（rounds[].choices[].diffPrice，实测换大薯条 +¥2.5）
  * 减法特制不改价（去吉士 ¥23 → ¥23）。
  * 券是**独立加购商品**，不是折扣券：必须作为一个 item 传
    {productCode, quantity:1, couponId, couponCode} 才生效，
    挂到普通餐品 item 上无效（返回 None）。券不抵扣已有餐品金额。

子命令：
  stores   双路获取门店（收藏优先，空则回退位置搜索）
  menu     拉取门店当前在售菜单 + 官方营养表
  inspect  拆解单个餐品，列出真实可定制空间
  verify   复核配方：在售性 / 可点时段 / 可替换项
  price    官方核价：配方原价 + 加购券三档对比
  save     把配方追加进种子库
  card     渲染单文件 HTML 配方卡
  bundle   抓取离线样例包（供 --demo 使用）
  demo     离线跑通全流程（无需 Token）

Token 读取优先级：--token > 环境变量 MCD_MCP_TOKEN > .env 文件。Token 不落盘。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcd_mcp_client import (  # noqa: E402
    DEFAULT_URL, McpError, call_tool, load_token, unwrap,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")
BUNDLE_PATH = os.path.join(EXAMPLES, "demo_bundle.json")
SEED_PATH = os.path.join(EXAMPLES, "seed_recipes.json")

# 官方限流 600 次/分钟，扇出时保持温和间隔
THROTTLE = 0.12

# ------------------------------------------------------ 工具函数


def dump(payload, out_path):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("已写入：%s" % os.path.abspath(out_path))
    else:
        print(text)


def yuan(cents):
    """分 -> 元字符串。"""
    try:
        return "%.2f" % (int(cents) / 100.0)
    except (TypeError, ValueError):
        return "-"


def normalize_name(text):
    """归一化菜名，便于跨写法匹配营养表。"""
    if not text:
        return ""
    table = {"（": "(", "）": ")", "【": "", "】": "", "“": "", "”": "",
             "「": "", "」": "", "＋": "+", " ": "", "\u3000": "", "™": ""}
    out = str(text)
    for src, dst in table.items():
        out = out.replace(src, dst)
    return out.strip()


def parse_nutrition_text(raw):
    """解析 list-nutrition-foods 返回的文本表（9 列）。

    第 2 列 nutritionDescription 必须剔除，否则菜名会被污染成「可乐中杯,null」。
    """
    rows = []
    if not isinstance(raw, str):
        return rows
    header_cols = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and "{" in line:
            header_cols = [c.strip() for c in
                           line[line.find("{") + 1:line.find("}")].split(",")]
            continue
        if header_cols is None or line.startswith("#") or line.startswith("-"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        name = ",".join(parts[:len(parts) - 8])
        rest = parts[len(parts) - 8:]

        def num(idx, default=0.0):
            try:
                return float(rest[idx])
            except (ValueError, IndexError):
                return default

        rows.append({"productName": name, "energyKj": num(1), "kcal": num(2),
                     "protein": num(3), "fat": num(4), "carbohydrate": num(5),
                     "sodium": num(6), "calcium": num(7)})
    return rows


def build_nutrition_index(rows):
    idx = {}
    for r in rows:
        key = normalize_name(r["productName"])
        if key and key not in idx:
            idx[key] = r
    return idx


def match_nutrition(name, index):
    """三级匹配：原名 -> 归一化 -> 归一化双向包含（取最长）。"""
    if not name:
        return None
    if name in index:
        return index[name]
    target = normalize_name(name)
    if target in index:
        return index[target]
    best, best_len = None, 0
    for key, row in index.items():
        if key and (key in target or target in key):
            if len(key) > best_len:
                best, best_len = row, len(key)
    return best


def parse_time_windows(text):
    """解析 reservationOptionText，如「宵夜(00:00至04:45,22:14至23:59)，早餐(...)」。

    返回 [(名称, 起, 止)]，起止为分钟数。
    """
    out = []
    if not text:
        return out
    # 段间分隔符是全角「，」；同一段内的多个区间用半角「,」连接
    # （例如「宵夜(00:00至04:45,22:14至23:59)，早餐(05:14至10:15)」）。
    # 若用 [，,] 一起分隔，多区间餐段会被切碎，切勿改回。
    for seg in text.split("，"):
        m = re.match(r"^\s*([^()]+)\((.+)\)\s*$", seg)
        if not m:
            continue
        label, ranges = m.group(1).strip(), m.group(2)
        for r in ranges.split(","):
            m2 = re.match(r"^\s*(\d{2}):(\d{2})\s*至\s*(\d{2}):(\d{2})\s*$", r)
            if not m2:
                continue
            h1, m1, h2, m2v = (int(x) for x in m2.groups())
            out.append((label, h1 * 60 + m1, h2 * 60 + m2v))
    return out


def current_daypart(store, now_minutes):
    """判定门店当前所处餐段（基于官方 reservationTimeOptions）。"""
    for opt in store.get("reservationTimeOptions") or []:
        if not opt.get("today"):
            continue
        for label, start, end in parse_time_windows(opt.get("reservationOptionText") or ""):
            if start <= now_minutes <= end:
                return label
    return ""


# ------------------------------------------------------ 数据抓取


def fetch_store(ctx, city="", keyword=""):
    """双路获取门店：收藏优先（searchType=1），空则回退位置搜索（searchType=2）。

    实测：单次恒返回 5 家，无分页；经纬度恒 0.0。
    """
    token, url = ctx["token"], ctx["url"]
    attempts = []
    try:
        fav = unwrap(call_tool("query-nearby-stores",
                               {"beType": ctx["be_type"], "searchType": 1}, token, url))
        fav = fav if isinstance(fav, list) else []
    except McpError as exc:
        fav, exc_text = [], str(exc)
        attempts.append("收藏门店查询失败（%s），回退位置搜索" % exc_text[:80])
    else:
        if not fav:
            attempts.append("收藏门店为空，回退位置搜索")

    if fav:
        return fav, attempts or ["使用收藏门店"]

    if not city or not keyword:
        raise McpError("收藏门店为空且未提供 --city/--keyword（位置搜索必填），无法获取门店。")
    loc = unwrap(call_tool("query-nearby-stores",
                           {"beType": ctx["be_type"], "searchType": 2,
                            "city": city, "keyword": keyword}, token, url))
    loc = loc if isinstance(loc, list) else []
    if not loc:
        raise McpError("位置搜索未返回门店（错误码 600014 表示未查到该地址）。")
    return loc, attempts + ["位置搜索命中 %d 家" % len(loc)]


def fetch_menu(ctx, store_code):
    data = unwrap(call_tool("query-meals",
                            {"storeCode": store_code, "orderType": ctx["order_type"],
                             "beType": ctx["be_type"]}, ctx["token"], ctx["url"]))
    return data or {}


def fetch_detail(ctx, store_code, code):
    return unwrap(call_tool("query-meal-detail",
                            {"storeCode": store_code, "orderType": ctx["order_type"],
                             "beType": ctx["be_type"], "code": code},
                            ctx["token"], ctx["url"]))


def fetch_nutrition(ctx):
    return parse_nutrition_text(unwrap(call_tool("list-nutrition-foods", {},
                                                 ctx["token"], ctx["url"])))


def fetch_coupons(ctx, store_code):
    """只有 query-store-coupons 返回结构化 couponId，可进核价。"""
    try:
        data = unwrap(call_tool("query-store-coupons",
                                {"storeCode": store_code, "orderType": ctx["order_type"],
                                 "beType": ctx["be_type"]}, ctx["token"], ctx["url"]))
    except McpError as exc:
        print("  [告警] 券查询失败：%s（降级为无券）" % exc, file=sys.stderr)
        return []
    return data if isinstance(data, list) else []


def calculate(ctx, store_code, items):
    payload = {"storeCode": store_code, "orderType": ctx["order_type"],
               "beType": ctx["be_type"], "items": items}
    if ctx.get("be_code"):
        payload["beCode"] = ctx["be_code"]
    return unwrap(call_tool("calculate-price", payload, ctx["token"], ctx["url"]))


# ------------------------------------------------------ 配方


def load_recipe(ref):
    """ref 可以是种子库里的 id，也可以是 JSON 文件路径。"""
    if os.path.isfile(ref):
        with open(ref, encoding="utf-8") as fh:
            return json.load(fh)
    if os.path.isfile(SEED_PATH):
        with open(SEED_PATH, encoding="utf-8") as fh:
            seeds = json.load(fh)
        for r in seeds.get("recipes") or []:
            if r.get("id") == ref or r.get("name") == ref:
                return r
    raise McpError("找不到配方 %r（既不是文件，也不在种子库 %s 中）" % (ref, SEED_PATH))


def recipe_items(recipe):
    return recipe.get("items") or []


# ------------------------------------------------------ 子命令：inspect


def cmd_inspect(args, ctx):
    detail = fetch_detail(ctx, args.store_code, args.code)
    if not isinstance(detail, dict):
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 0

    result = {
        "storeCode": args.store_code,
        "code": detail.get("code"),
        "name": detail.get("name"),
        "supportModify": detail.get("supportModify"),
        "free_toggles": [],     # 减法/二值开关，不改价
        "paid_options": [],     # 加价特制，真实进价
        "rounds": [],           # 套餐子项替换，真实进价
    }

    def scan_modification(mod, where):
        for item in (mod or {}).get("items") or []:
            for v in item.get("values") or []:
                entry = {"scope": where, "code": v.get("code"), "name": v.get("name"),
                         "price_cents": v.get("price") or 0,
                         "maxQuantity": v.get("maxQuantity"),
                         "selectedKey": v.get("selectedKey"),
                         "unselectedKey": v.get("unselectedKey")}
                if (v.get("price") or 0) > 0:
                    result["paid_options"].append(entry)
                elif v.get("unselectedKey"):
                    result["free_toggles"].append(entry)

    scan_modification(detail.get("modification"), "本体")
    for r in detail.get("rounds") or []:
        choices = []
        for c in r.get("choices") or []:
            choices.append({"code": c.get("code"), "name": c.get("name"),
                            "isDefault": c.get("isDefault"),
                            "diffPrice": c.get("diffPrice"),
                            "supportModify": c.get("supportModify")})
            scan_modification(c.get("modification"), "round%s:%s" % (r.get("id"), c.get("name")))
        result["rounds"].append({"round": r.get("id"), "name": r.get("name"),
                                 "minQuantity": r.get("minQuantity"),
                                 "maxQuantity": r.get("maxQuantity"),
                                 "choices": choices})

    dump(result, args.out)
    if not args.out:
        print("\n餐品：%s（%s）" % (result["name"], result["code"]))
        print("加价特制（真实进价）：%d 项" % len(result["paid_options"]))
        for p in result["paid_options"]:
            print("   + %s（code %s）price=%s 分 → key=%s" %
                  (p["name"], p["code"], p["price_cents"], p["selectedKey"]))
        print("免费开关（不改价）：%d 项 -> %s" %
              (len(result["free_toggles"]),
               "、".join(t["name"] for t in result["free_toggles"]) or "无"))
        print("套餐子项替换：%d 个 round" % len(result["rounds"]))
    return 0


# ------------------------------------------------------ 子命令：verify


def verify_recipe(ctx, store, recipe, menu, details, nutrition_index, now_minutes):
    """复核配方：在售性 / 可点时段 / 可替换项 / 真实组成。"""
    meals = menu.get("meals") or {}
    daypart = current_daypart(store, now_minutes)
    components = []

    for it in recipe_items(recipe):
        code = str(it.get("productCode") or "")
        qty = int(it.get("quantity") or 1)
        menu_entry = meals.get(code) or {}
        detail = details.get(code) or {}
        on_sale = bool(menu_entry)
        comp = {
            "productCode": code,
            "quantity": qty,
            "menu_name": menu_entry.get("name") or detail.get("name") or "",
            "detail_name": detail.get("name") or "",
            "menu_price_yuan": menu_entry.get("currentPrice"),
            "on_sale": on_sale,
            "substitutes": [],
            "mods": [],
        }
        if not on_sale:
            comp["status"] = "不可售"
            comp["advice"] = "该餐品不在当前门店在售菜单中（可能已下架或不在当前餐段）"
            components.append(comp)
            continue

        # 套餐子项替换：记录 round 里可替换的默认值与差价
        for r in detail.get("rounds") or []:
            for c in r.get("choices") or []:
                if c.get("isDefault"):
                    continue
                comp["substitutes"].append({
                    "round": r.get("id"), "round_name": r.get("name"),
                    "code": c.get("code"), "name": c.get("name"),
                    "diffPrice": c.get("diffPrice")})
        # 加价特制
        def scan(mod, where):
            for item in (mod or {}).get("items") or []:
                for v in item.get("values") or []:
                    if (v.get("price") or 0) > 0:
                        comp["mods"].append({"scope": where, "name": v.get("name"),
                                             "code": v.get("code"),
                                             "price_cents": v.get("price"),
                                             "key": v.get("selectedKey")})
        scan(detail.get("modification"), "本体")
        comp["status"] = "可售"
        comp["advice"] = ""
        components.append(comp)

    return {
        "store": {"storeCode": store.get("storeCode"), "storeName": store.get("storeName"),
                  "address": store.get("address"), "businessStatus": store.get("businessStatus"),
                  "hours": "%s-%s" % (store.get("businessStartTime"), store.get("businessEndTime"))},
        "daypart": daypart,
        "recipe": {"id": recipe.get("id"), "name": recipe.get("name"),
                   "story": recipe.get("story", "")},
        "components": components,
        "all_on_sale": all(c["on_sale"] for c in components),
    }


def cmd_verify(args, ctx):
    recipe = load_recipe(args.recipe)
    if args.demo:
        bundle = load_bundle()
        store = bundle["store"]
        menu = bundle["menu"]
        details = bundle["details"]
        nutrition_index = build_nutrition_index(bundle["nutrition"])
        now_minutes = bundle["now_minutes"]
    else:
        stores, _ = fetch_store(ctx, args.city, args.keyword)
        store = pick_store(stores, args.store_code)
        menu = fetch_menu(ctx, store["storeCode"])
        nutrition_rows = fetch_nutrition(ctx)
        nutrition_index = build_nutrition_index(nutrition_rows)
        details = {}
        for it in recipe_items(recipe):
            code = str(it.get("productCode") or "")
            if code and code not in details:
                try:
                    details[code] = fetch_detail(ctx, store["storeCode"], code)
                except McpError as exc:
                    print("  [告警] 拆解 %s 失败：%s" % (code, exc), file=sys.stderr)
                time.sleep(THROTTLE)
        now_minutes = server_now_minutes(ctx)

    result = verify_recipe(ctx, store, recipe, menu, details, nutrition_index, now_minutes)
    dump(result, args.out)
    if not args.out:
        print("\n配方：%s" % result["recipe"]["name"])
        print("门店：%s（%s）营业 %s | 当前餐段：%s" %
              (result["store"]["storeName"], result["store"]["storeCode"],
               result["store"]["hours"], result["daypart"] or "未知"))
        for c in result["components"]:
            flag = "OK " if c["on_sale"] else "NG "
            print("  %s x%s %s [%s] %s" %
                  (flag, c["quantity"], c["menu_name"] or c["productCode"],
                   c["status"], c["advice"]))
            if c["substitutes"]:
                print("       可替换：%s" % "、".join(
                    "%s(%s)" % (s["name"], s["diffPrice"]) for s in c["substitutes"][:6]))
            if c["mods"]:
                print("       加价特制：%s" % "、".join(
                    "%s +%s分" % (m["name"], m["price_cents"]) for m in c["mods"][:6]))
        print("整体可售：%s" % ("是" if result["all_on_sale"] else "否"))
    return 0


# ------------------------------------------------------ 子命令：price


def price_recipe(ctx, store_code, recipe, coupons):
    """官方核价：配方原价 + 每张券作为加购件的三档对比。"""
    items = []
    for it in recipe_items(recipe):
        item = {"productCode": it.get("productCode"), "quantity": int(it.get("quantity") or 1)}
        if it.get("roundList"):
            item["roundList"] = it["roundList"]
        if it.get("modification"):
            item["modification"] = it["modification"]
        items.append(item)

    base = calculate(ctx, store_code, items)
    base = base if isinstance(base, dict) else {}
    # 实测：calculate-price 会带出非券的官方自动优惠（discount>0），
    # 因此「原价」取 originalPrice，「实付」取 price，两者必须分开报，不能混称。
    result = {
        "storeCode": store_code,
        "recipe": {"id": recipe.get("id"), "name": recipe.get("name")},
        "base": base,
        "base_original_yuan": yuan(base.get("originalPrice")),
        "base_yuan": yuan(base.get("price")),
        "base_discount_yuan": yuan(base.get("discount")),
        "base_has_auto_discount": (base.get("discount") or 0) > 0,
        "coupon_trials": [],
    }

    for c in coupons:
        products = c.get("products") or []
        if not products:
            continue
        cprod = products[0]
        coupon_item = {"productCode": cprod.get("productCode"), "quantity": 1,
                       "couponId": c.get("couponId"), "couponCode": c.get("couponCode")}
        try:
            alone = calculate(ctx, store_code, [coupon_item])
            together = calculate(ctx, store_code, items + [coupon_item])
        except McpError as exc:
            result["coupon_trials"].append({"title": c.get("title"), "error": str(exc)})
            continue
        time.sleep(THROTTLE)
        alone_discount = alone.get("discount") or 0 if isinstance(alone, dict) else 0
        together = together if isinstance(together, dict) else {}
        result["coupon_trials"].append({
            "title": c.get("title"),
            "coupon_product": cprod.get("productName"),
            "coupon_product_code": cprod.get("productCode"),
            "validity": c.get("tradeDateTime"),
            "addon_original_cents": alone.get("originalPrice"),
            "addon_pay_cents": alone.get("price"),
            "addon_saved_cents": alone_discount,
            "total_original_cents": together.get("originalPrice"),
            "total_with_addon_cents": together.get("price"),
            "total_discount_cents": together.get("discount"),
        })

    # 最优加购件：按「券自身省得最多」排序（注意：总价一定 >= 配方原价）
    trials = [t for t in result["coupon_trials"] if "error" not in t]
    trials.sort(key=lambda t: -(t.get("addon_saved_cents") or 0))
    result["best_addon"] = trials[0] if trials else None
    return result


def cmd_price(args, ctx):
    recipe = load_recipe(args.recipe)
    if args.demo:
        bundle = load_bundle()
        store_code = bundle["store"]["storeCode"]
        coupons = bundle.get("coupons") or []
        result = price_recipe_offline(bundle, recipe, coupons)
    else:
        stores, _ = fetch_store(ctx, args.city, args.keyword)
        store = pick_store(stores, args.store_code)
        store_code = store["storeCode"]
        coupons = fetch_coupons(ctx, store_code)
        result = price_recipe(ctx, store_code, recipe, coupons)

    dump(result, args.out)
    if not args.out:
        print("\n配方：%s | 门店 %s" % (result["recipe"]["name"], result["storeCode"]))
        print("官方原价：¥%s" % result["base_original_yuan"])
        print("实付（含官方自动优惠）：¥%s" % result["base_yuan"])
        if result["base_has_auto_discount"]:
            print("  自动优惠：¥%s（非券，由 calculate-price 的 discount 字段带出）"
                  % result["base_discount_yuan"])
        if not result["coupon_trials"]:
            print("本门店当前无可用券（或券查询降级）")
        for t in result["coupon_trials"]:
            if "error" in t:
                print("  [券失败] %s：%s" % (t["title"], t["error"]))
                continue
            print("  加购《%s》%s" % (t["title"], t.get("validity") or ""))
            print("     券商品原价 ¥%s → 用券 ¥%s（省 ¥%s）" %
                  (yuan(t.get("addon_original_cents")), yuan(t.get("addon_pay_cents")),
                   yuan(t.get("addon_saved_cents"))))
            print("     配方 + 该加购件 = ¥%s（省 ¥%s）" %
                  (yuan(t.get("total_with_addon_cents")), yuan(t.get("total_discount_cents"))))
        if result["best_addon"]:
            print("省得最多的加购件：%s（省 ¥%s）" %
                  (result["best_addon"]["title"],
                   yuan(result["best_addon"]["addon_saved_cents"])))
        print("\n注：麦当劳门店券是独立加购商品，不抵扣已有餐品金额——加购后总价必然 ≥ 配方原价。")
    return 0


def price_recipe_offline(bundle, recipe, coupons):
    """离线核价：直接复用样例包里已抓好的真实结果（不重算，不伪造）。"""
    cached = (bundle.get("price") or {}).get(recipe.get("id"))
    if not cached:
        raise McpError("样例包中没有配方 %s 的核价结果，请先联网跑一次 price。" % recipe.get("id"))
    return cached


# ------------------------------------------------------ 营养聚合与分位


def aggregate_nutrition(recipe, details, nutrition_index):
    """按真实子项聚合营养。套餐用 rounds 默认子项；匹配不到就如实标注。"""
    total = {"kcal": 0.0, "sodium": 0.0, "protein": 0.0, "fat": 0.0, "carbohydrate": 0.0}
    known = 0
    unknown = []
    pieces = 0
    for it in recipe_items(recipe):
        code = str(it.get("productCode") or "")
        qty = int(it.get("quantity") or 1)
        detail = details.get(code) or {}
        rounds = detail.get("rounds") or []
        targets = []
        if rounds:
            for r in rounds:
                for c in r.get("choices") or []:
                    if c.get("isDefault"):
                        targets.append(c.get("name"))
        else:
            targets.append(detail.get("name"))
        for name in targets:
            row = match_nutrition(name, nutrition_index)
            if row:
                known += 1
                for k in total:
                    total[k] += (row.get(k) or 0) * qty
            else:
                unknown.append(name)
        pieces += qty * max(1, len(targets))
    for k in total:
        total[k] = round(total[k], 1)
    return {"total": total, "known_items": known, "unknown_items": unknown, "pieces": pieces}


def nutrition_percentile(menu, details, nutrition_index, value, key="kcal"):
    """基准全部来自麦当劳自家数据：本店已匹配到营养的在售餐品分布。"""
    dist = []
    for code, entry in (menu.get("meals") or {}).items():
        if not isinstance(entry, dict):
            continue
        detail = details.get(code) or {}
        rounds = detail.get("rounds") or []
        if rounds:
            s = 0.0
            hit = 0
            for r in rounds:
                for c in r.get("choices") or []:
                    if c.get("isDefault"):
                        row = match_nutrition(c.get("name"), nutrition_index)
                        if row:
                            s += row.get(key) or 0
                            hit += 1
            if hit:
                dist.append(s)
        else:
            row = match_nutrition(entry.get("name"), nutrition_index)
            if row:
                dist.append(row.get(key) or 0)
    if not dist:
        return {"percentile": None, "sample": 0, "median": None}
    dist.sort()
    below = sum(1 for x in dist if x < value)
    return {"percentile": round(below / len(dist) * 100), "sample": len(dist),
            "median": round(dist[len(dist) // 2], 1)}


# ------------------------------------------------------ 门店选择 / 时间


def pick_store(stores, store_code=""):
    if not stores:
        raise McpError("未获取到任何门店。")
    if store_code:
        for s in stores:
            if str(s.get("storeCode")) == str(store_code):
                return s
        raise McpError("门店 %s 不在返回列表中（单次仅返回 5 家，可换 --city/--keyword 再试）。" % store_code)
    return stores[0]


def server_now_minutes(ctx):
    """取麦当劳服务器当前时间，用于餐段判定（不依赖本地时钟）。"""
    try:
        data = unwrap(call_tool("now-time-info", {}, ctx["token"], ctx["url"]))
        fmt = (data or {}).get("formatted") or ""
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})", fmt)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
    except McpError:
        pass
    now = dt.datetime.now()
    return now.hour * 60 + now.minute


# ------------------------------------------------------ 离线样例包


def load_bundle():
    if not os.path.isfile(BUNDLE_PATH):
        raise McpError("缺少离线样例包 %s，请先联网执行 `bundle` 子命令。" % BUNDLE_PATH)
    with open(BUNDLE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_bundle(args, ctx):
    """联网抓取一份真实数据，落盘为离线样例包（供 --demo 使用，不含 Token）。"""
    stores, notes = fetch_store(ctx, args.city, args.keyword)
    store = pick_store(stores, args.store_code)
    code = store["storeCode"]
    print("门店：%s（%s）" % (store["storeName"], code))
    for n in notes:
        print("  %s" % n)

    menu = fetch_menu(ctx, code)
    print("在售餐品：%d" % len(menu.get("meals") or {}))
    nutrition = fetch_nutrition(ctx)
    print("营养条目：%d" % len(nutrition))
    coupons = fetch_coupons(ctx, code)
    print("可用券：%d" % len(coupons))

    seeds = []
    if os.path.isfile(SEED_PATH):
        with open(SEED_PATH, encoding="utf-8") as fh:
            seeds = (json.load(fh).get("recipes") or [])

    details, prices = {}, {}
    for r in seeds:
        for it in recipe_items(r):
            c = str(it.get("productCode") or "")
            if c and c not in details:
                try:
                    details[c] = fetch_detail(ctx, code, c)
                except McpError as exc:
                    print("  [告警] 拆解 %s 失败：%s" % (c, exc), file=sys.stderr)
                time.sleep(THROTTLE)
        try:
            prices[r["id"]] = price_recipe(ctx, code, r, coupons)
            print("  配方 %s 核价：¥%s" % (r["id"], prices[r["id"]]["base_yuan"]))
        except McpError as exc:
            print("  [告警] 配方 %s 核价失败：%s" % (r.get("id"), exc), file=sys.stderr)
        time.sleep(THROTTLE)

    bundle = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "mcd-mcp live",
        "note": "全部字段来自麦当劳官方 MCP 真实返回，未做任何伪造或插值。",
        "store": store,
        "menu": menu,
        "nutrition": nutrition,
        "coupons": coupons,
        "details": details,
        "price": prices,
        "now_minutes": server_now_minutes(ctx),
    }
    dump(bundle, BUNDLE_PATH)
    return 0


def cmd_stores(args, ctx):
    stores, notes = fetch_store(ctx, args.city, args.keyword)
    payload = {"fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
               "source_tool": "query-nearby-stores", "notes": notes,
               "count": len(stores), "stores": stores}
    dump(payload, args.out)
    if not args.out:
        for n in notes:
            print("  %s" % n)
        for s in stores:
            print("%s | %s | %s | %sm | %s-%s" % (
                s.get("storeCode"), s.get("storeName"), s.get("address"),
                s.get("distance"), s.get("businessStartTime"), s.get("businessEndTime")))
        print("\n注：单次恒返回 5 家、无分页；经纬度恒为 0.0，不可用于地图渲染。")
    return 0


def cmd_menu(args, ctx):
    menu = fetch_menu(ctx, args.store_code)
    nutrition = fetch_nutrition(ctx)
    index = build_nutrition_index(nutrition)
    rows = []
    for code, d in sorted((menu.get("meals") or {}).items(), key=lambda kv: str(kv[1].get("name"))):
        if not isinstance(d, dict):
            continue
        row = match_nutrition(d.get("name"), index)
        rows.append({"code": code, "name": d.get("name"),
                     "price_yuan": d.get("currentPrice"),
                     "kcal": row.get("kcal") if row else None,
                     "sodium": row.get("sodium") if row else None})
    payload = {"fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
               "storeCode": args.store_code, "count": len(rows),
               "nutrition_matched": sum(1 for r in rows if r["kcal"] is not None),
               "meals": rows}
    dump(payload, args.out)
    if not args.out:
        print("在售餐品 %d 款，其中 %d 款匹配到官方营养数据" %
              (len(rows), payload["nutrition_matched"]))
    return 0


def cmd_save(args, ctx):
    recipe = load_recipe(args.recipe)
    if args.name:
        recipe["name"] = args.name
    if not recipe.get("id"):
        recipe["id"] = "custom-%d" % int(time.time())
    seeds = {"recipes": []}
    if os.path.isfile(SEED_PATH):
        with open(SEED_PATH, encoding="utf-8") as fh:
            seeds = json.load(fh)
    ids = {r.get("id") for r in seeds.get("recipes") or []}
    if recipe["id"] in ids:
        seeds["recipes"] = [recipe if r.get("id") == recipe["id"] else r
                            for r in seeds["recipes"]]
        print("已更新配方：%s（%s）" % (recipe["name"], recipe["id"]))
    else:
        seeds.setdefault("recipes", []).append(recipe)
        print("已追加配方：%s（%s）" % (recipe["name"], recipe["id"]))
    dump(seeds, SEED_PATH)
    return 0


def cmd_card(args, ctx):
    from build_card import render_card
    recipe = load_recipe(args.recipe)
    if args.demo:
        bundle = load_bundle()
        store = bundle["store"]
        menu = bundle["menu"]
        details = bundle["details"]
        nutrition_index = build_nutrition_index(bundle["nutrition"])
        coupons = bundle.get("coupons") or []
        now_minutes = bundle["now_minutes"]
        price_result = price_recipe_offline(bundle, recipe, coupons)
    else:
        stores, _ = fetch_store(ctx, args.city, args.keyword)
        store = pick_store(stores, args.store_code)
        code = store["storeCode"]
        menu = fetch_menu(ctx, code)
        nutrition_index = build_nutrition_index(fetch_nutrition(ctx))
        details = {}
        for it in recipe_items(recipe):
            c = str(it.get("productCode") or "")
            if c and c not in details:
                try:
                    details[c] = fetch_detail(ctx, code, c)
                except McpError as exc:
                    print("  [告警] 拆解 %s 失败：%s" % (c, exc), file=sys.stderr)
                time.sleep(THROTTLE)
        coupons = fetch_coupons(ctx, code)
        price_result = price_recipe(ctx, code, recipe, coupons)
        now_minutes = server_now_minutes(ctx)

    verify = verify_recipe(ctx, store, recipe, menu, details, nutrition_index, now_minutes)
    nutrition = aggregate_nutrition(recipe, details, nutrition_index)
    percentile = nutrition_percentile(menu, details, nutrition_index,
                                      nutrition["total"]["kcal"], "kcal")
    out = args.out or os.path.join(EXAMPLES, "demo_card.html")
    render_card({"verify": verify, "price": price_result, "nutrition": nutrition,
                 "percentile": percentile, "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "offline": bool(args.demo)}, out)
    print("配方卡已生成：%s" % os.path.abspath(out))
    return 0


def cmd_demo(args, ctx):
    """离线全流程：读样例包 -> 复核 -> 核价 -> 渲染卡片。"""
    args.demo = True
    bundle = load_bundle()
    print("离线样例包生成于 %s" % bundle.get("generated_at"))
    print("门店：%s（%s）" % (bundle["store"].get("storeName"), bundle["store"].get("storeCode")))
    for r in (json.load(open(SEED_PATH, encoding="utf-8")).get("recipes") or []):
        args.recipe = r["id"]
        print("\n=== %s（%s）===" % (r.get("name"), r.get("id")))
        # args.out 必须在每轮重置：否则 verify/price 会把 JSON 覆盖写进上一轮的 HTML 卡片
        args.out = ""
        cmd_verify(args, ctx)
        args.out = ""
        cmd_price(args, ctx)
        args.out = os.path.join(EXAMPLES, "demo_card_%s.html" % r["id"])
        cmd_card(args, ctx)
    print("\n离线流程完成，共渲染 %d 张配方卡。" % len(
        json.load(open(SEED_PATH, encoding="utf-8")).get("recipes") or []))
    return 0


# ------------------------------------------------------ CLI


def build_parser():
    p = argparse.ArgumentParser(
        description="麦麦组合实验室：官方可核价的非常规搭配引擎")
    p.add_argument("action", choices=["stores", "menu", "inspect", "verify", "price",
                                      "save", "card", "bundle", "demo"])
    p.add_argument("--token", default="")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--out", default="")
    p.add_argument("--demo", action="store_true", help="离线模式，读 examples/demo_bundle.json")
    # 门店
    p.add_argument("--city", default="")
    p.add_argument("--keyword", default="")
    p.add_argument("--store-code", default="")
    p.add_argument("--be-type", type=int, default=1, help="1-到店自提，5-得来速")
    p.add_argument("--order-type", type=int, default=1, help="1-到店，2-外送")
    p.add_argument("--be-code", default="")
    # 配方
    p.add_argument("--recipe", default="", help="配方 id 或 JSON 文件路径")
    p.add_argument("--code", default="", help="inspect 用：餐品 code")
    p.add_argument("--name", default="", help="save 用：覆盖配方名")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.action == "demo":
        ctx = {"token": "", "url": args.url, "be_type": args.be_type,
               "order_type": args.order_type, "be_code": args.be_code}
        try:
            return cmd_demo(args, ctx)
        except McpError as exc:
            print("失败：%s" % exc, file=sys.stderr)
            return 1

    token = load_token(args.token)
    if not token:
        print("缺少 MCP Token：使用 --token、设置环境变量 MCD_MCP_TOKEN，或写入 .env。",
              file=sys.stderr)
        return 2
    ctx = {"token": token, "url": args.url, "be_type": args.be_type,
           "order_type": args.order_type, "be_code": args.be_code}

    handlers = {"stores": cmd_stores, "menu": cmd_menu, "inspect": cmd_inspect,
                "verify": cmd_verify, "price": cmd_price, "save": cmd_save,
                "card": cmd_card, "bundle": cmd_bundle}
    try:
        return handlers[args.action](args, ctx)
    except McpError as exc:
        print("MCP 调用失败：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
