#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双表核对脚本（纯标准库实现）

按用户指定的匹配键（可多列组合）对两份 CSV 做逐行比对，输出 Markdown 报告。
只读不写：绝不修改、覆盖或新建原始文件，也不保留数据副本。

用法：
  python scripts/diff_tables.py A.csv B.csv --key id --cols amount,qty --tolerance 0.01
  python scripts/diff_tables.py A.csv B.csv --key order_id,sku          # 复合键
  python scripts/diff_tables.py A.csv B.csv --key id --ignore-case     # 键与字符串比较忽略大小写

设计要点（与技能正文一致）：
  - 重复键单独成节，不静默取第一条
  - 仅 A 有 / 仅 B 有 分别列出
  - 数值比对用容差，浮点不用直接等号
  - 区分 空字符串 / "0" / 缺失(列不存在) 三态
  - 报告末尾给出可复算说明：命令、键、容差、参与行数
"""

import argparse
import csv
import os
import sys

# 状态标记：用于三态区分
STATE_MISSING = "缺失"   # 该列在表中不存在
STATE_BLANK = "空字符串"  # 列存在但值为空
STATE_VALUE = "有值"     # 列存在且值为非空


def read_csv(path, ignore_case, strip):
    """读取 CSV，返回 (表头列表, 行字典列表)。自动尝试 UTF-8(BOM)/UTF-8/GBK。"""
    encodings = ["utf-8-sig", "utf-8", "gbk"]
    raw = None
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                raw = f.read()
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue
    if raw is None:
        raise RuntimeError("无法解码文件 %s：%s" % (path, last_err))

    reader = csv.DictReader(raw.splitlines())
    if reader.fieldnames is None:
        raise RuntimeError("文件 %s 没有表头行" % path)
    header = list(reader.fieldnames)
    rows = []
    for r in reader:
        if strip:
            cleaned = {}
            for k, v in r.items():
                nk = k.strip() if k is not None else k
                cleaned[nk] = v.strip() if isinstance(v, str) else v
            rows.append(cleaned)
        else:
            rows.append(dict(r))
    return header, rows


def norm_key(row, key_cols, ignore_case):
    """根据匹配键列计算键（复合键用 ‖ 连接）。"""
    parts = []
    for c in key_cols:
        val = row.get(c, "")
        if val is None:
            val = ""
        if ignore_case:
            val = val.lower()
        parts.append(val)
    return "‖".join(parts)


def cell_state(row, col):
    """返回单元格三态：(状态, 原始字符串)。"""
    if col not in row:
        return STATE_MISSING, None
    val = row[col]
    if val is None or val == "":
        return STATE_BLANK, ""
    return STATE_VALUE, val


def try_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def build_index(rows, key_cols, ignore_case):
    """建立 键 -> [行] 的映射，并统计重复键。"""
    idx = {}
    for i, r in enumerate(rows):
        k = norm_key(r, key_cols, ignore_case)
        idx.setdefault(k, []).append((i, r))
    dup = {k: v for k, v in idx.items() if len(v) > 1}
    return idx, dup


def state_label(row, col):
    st, val = cell_state(row, col)
    if st == STATE_MISSING:
        return "(缺失)"
    if st == STATE_BLANK:
        return "(空字符串)"
    return val


def main():
    parser = argparse.ArgumentParser(
        description="按匹配键逐行核对两份 CSV，输出 Markdown 差异报告"
    )
    parser.add_argument("a", help="A 表 CSV 路径")
    parser.add_argument("b", help="B 表 CSV 路径")
    parser.add_argument("--key", required=True,
                        help="匹配键列名，多列用英文逗号分隔，如 id 或 order_id,sku")
    parser.add_argument("--cols", default=None,
                        help="要比对的数值列，逗号分隔，如 amount,qty；不填则比对除键外的所有列")
    parser.add_argument("--tolerance", type=float, default=0.01,
                        help="数值容差，默认 0.01；差值绝对值 <= 容差视为一致")
    parser.add_argument("--ignore-case", action="store_true",
                        help="键与字符串比较时忽略大小写")
    parser.add_argument("--no-strip", action="store_true",
                        help="不去除键与值的前后空格（默认会去除）")
    args = parser.parse_args()

    strip = not args.no_strip
    key_cols = [c.strip() for c in args.key.split(",") if c.strip()]
    cmp_cols = None
    if args.cols:
        cmp_cols = [c.strip() for c in args.cols.split(",") if c.strip()]

    if not os.path.exists(args.a):
        sys.exit("错误：找不到 A 表文件 %s" % args.a)
    if not os.path.exists(args.b):
        sys.exit("错误：找不到 B 表文件 %s" % args.b)

    ha, rows_a = read_csv(args.a, args.ignore_case, strip)
    hb, rows_b = read_csv(args.b, args.ignore_case, strip)

    # 校验匹配键列存在
    for c in key_cols:
        if c not in ha and c not in hb:
            sys.exit("错误：匹配键列「%s」在 A、B 两表中都不存在。\n"
                     "A 表列：%s\nB 表列：%s" % (c, ha, hb))

    idx_a, dup_a = build_index(rows_a, key_cols, args.ignore_case)
    idx_b, dup_b = build_index(rows_b, key_cols, args.ignore_case)

    # 决定比对列
    if cmp_cols is None:
        cmp_cols = [c for c in ha if c not in key_cols]
        # 若 A 没有则退而用 B 的列
        if not cmp_cols:
            cmp_cols = [c for c in hb if c not in key_cols]

    keys_a = set(idx_a.keys())
    keys_b = set(idx_b.keys())
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = keys_a & keys_b

    diff_rows = []          # 双方都有但存在差异
    matched_same = 0

    for k in sorted(common):
        # 任一侧重复键 -> 不参与 1:1 比对，进「需确认」
        if k in dup_a or k in dup_b:
            continue
        ra = idx_a[k][0][1]
        rb = idx_b[k][0][1]
        row_has_diff = False
        for col in cmp_cols:
            sa, va = cell_state(ra, col)
            sb, vb = cell_state(rb, col)
            # 三态不一致：状态不同直接判差异
            if sa != sb:
                diff_rows.append((k, col, state_label(ra, col),
                                  state_label(rb, col), "", "状态不同(%s vs %s)" % (sa, sb)))
                row_has_diff = True
                continue
            if sa == STATE_BLANK or sa == STATE_MISSING:
                # 两侧都是空/缺失，视为一致
                continue
            # 两侧都有值
            fa = try_float(va)
            fb = try_float(vb)
            if fa is not None and fb is not None:
                delta = fb - fa
                if abs(delta) <= args.tolerance:
                    continue  # 数值一致（在容差内）
                diff_rows.append((k, col, va, vb, ("%.6g" % delta), "数值差异"))
                row_has_diff = True
            else:
                # 至少一侧非数值 -> 字符串精确比较
                if va != vb:
                    diff_rows.append((k, col, va, vb, "", "文本差异"))
                    row_has_diff = True
        if not row_has_diff:
            matched_same += 1

    # ---- 组装 Markdown ----
    out = []
    out.append("# 双表核对报告")
    out.append("")
    out.append("## 核对概览")
    out.append("")
    out.append("- A 表：`%s` —— %d 行，列：%s" % (args.a, len(rows_a), "、".join(ha)))
    out.append("- B 表：`%s` —— %d 行，列：%s" % (args.b, len(rows_b), "、".join(hb)))
    out.append("- 匹配键：%s" % " + ".join(key_cols))
    out.append("- 比对列：%s" % ("、".join(cmp_cols) if cmp_cols else "(无，请检查列名)"))
    out.append("- 数值容差：%s" % args.tolerance)
    if args.ignore_case:
        out.append("- 字符串比较：忽略大小写")
    out.append("- 仅 A 有：%d 个键" % len(only_a))
    out.append("- 仅 B 有：%d 个键" % len(only_b))
    out.append("- 双方都有且一致：%d" % matched_same)
    out.append("- 双方都有但存在差异：%d" % len(diff_rows))
    out.append("- A 表重复键：%d 个（涉及 %d 行）" %
               (len(dup_a), sum(len(v) for v in dup_a.values())))
    out.append("- B 表重复键：%d 个（涉及 %d 行）" %
               (len(dup_b), sum(len(v) for v in dup_b.values())))
    out.append("")

    def snapshot(idx, k):
        r = idx[k][0][1]
        src_cols = ha if ha else hb
        cols = [c for c in src_cols if c not in key_cols][:5]
        items = []
        for c in cols:
            items.append("%s=%s" % (c, state_label(r, c)))
        return "；".join(items)

    if only_a:
        out.append("## 仅 A 有（在 B 中找不到匹配键）")
        out.append("")
        out.append("| 匹配键 | 关键列快照 |")
        out.append("|---|---|")
        for k in only_a:
            out.append("| %s | %s |" % (k, snapshot(idx_a, k)))
        out.append("")

    if only_b:
        out.append("## 仅 B 有（在 A 中找不到匹配键）")
        out.append("")
        out.append("| 匹配键 | 关键列快照 |")
        out.append("|---|---|")
        for k in only_b:
            out.append("| %s | %s |" % (k, snapshot(idx_b, k)))
        out.append("")

    if diff_rows:
        out.append("## 数值 / 内容差异")
        out.append("")
        out.append("| 匹配键 | 列 | A 值 | B 值 | 差值(B-A) | 类型 |")
        out.append("|---|---|---|---|---|---|")
        for k, col, va, vb, delta, typ in diff_rows:
            out.append("| %s | %s | %s | %s | %s | %s |" %
                       (k, col, va, vb, delta, typ))
        out.append("")

    # 需你确认的：重复键 + 空值说明
    has_review = bool(dup_a) or bool(dup_b)
    out.append("## 需你确认的（重复键 / 空值）")
    out.append("")
    if dup_a or dup_b:
        out.append("### 重复键")
        out.append("")
        out.append("> 以下键在同一表内出现多次，无法做 1:1 比对，请人工确认归属后再核对。")
        out.append("")
        for side, dup in (("A", dup_a), ("B", dup_b)):
            if not dup:
                continue
            out.append("**%s 表重复键：**" % side)
            out.append("")
            out.append("| 匹配键 | 行号(0起) | 整行快照 |")
            out.append("|---|---|---|")
            for k, lst in sorted(dup.items()):
                for i, r in lst:
                    out.append("| %s | %d | %s |" %
                               (k, i, "；".join("%s=%s" % (c, state_label(r, c))
                                                for c in (ha if side == "A" else hb))))
            out.append("")
    else:
        out.append("- 未发现重复键。")
        out.append("")

    # 空值三态说明（基于本次比对列是否出现空/缺失）
    out.append("### 空值与缺失说明")
    out.append("")
    out.append("- 报告中「(空字符串)」表示该列存在但值为空；「(缺失)」表示该列在本表中不存在。")
    out.append("- 二者与数值 `0` 严格区分：空字符串/缺失 不参与数值容差比较，一律记为差异或状态不同。")
    out.append("")

    out.append("## 可复算说明")
    out.append("")
    cmd = ("python scripts/diff_tables.py %s %s --key %s" %
           (args.a, args.b, ",".join(key_cols)))
    if cmp_cols:
        cmd += " --cols %s" % ",".join(cmp_cols)
    cmd += " --tolerance %s" % args.tolerance
    if args.ignore_case:
        cmd += " --ignore-case"
    if args.no_strip:
        cmd += " --no-strip"
    out.append("- 复现命令：`%s`" % cmd)
    out.append("- 参与比对的唯一键数：%d（A 唯一键 %d，B 唯一键 %d）" %
                (len(common), len(keys_a), len(keys_b)))
    out.append("- 容差：%s；忽略大小写：%s；去前后空格：%s" %
                (args.tolerance, args.ignore_case, strip))
    out.append("- 本脚本只读不写，未修改任何原文件。")
    out.append("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
