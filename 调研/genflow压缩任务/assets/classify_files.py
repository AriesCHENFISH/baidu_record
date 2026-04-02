import os
import shutil
import re
import csv

source_dir = "all_files"
dest_parent_dir = "categorized_files"
csv_path = "file_categories.csv"

categories = {
    1: "1_长篇结构化应用文档",
    2: "2_学术论文与专业文献",
    3: "3_二维表格与日程数据",
    4: "4_极短视觉描述",
    5: "5_文学段落与扩写片段",
    6: "6_纯信息罗列清单",
    0: "0_未分类或杂项",
}


os.makedirs(dest_parent_dir, exist_ok=True)
for cat in categories.values():
    os.makedirs(os.path.join(dest_parent_dir, cat), exist_ok=True)

results = []


def classify(content, filename):
    char_count = len(content.strip())
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    line_count = len(lines)
    avg_char_per_line = char_count / line_count if line_count > 0 else 0

    # 第1层：极短视觉描述 ，通常只有一小段，提到图片、展示等
    if char_count < 300 and any(
        kw in content for kw in ["图片", "展示了", "图中", "照片", "背景"]
    ):
        return 4

    # 第2层：学术论文与专业文献 ，包含摘要和专业格式
    if ("摘要" in content or "Abstract" in content) and (
        "关键词" in content
        or "参考文献" in content
        or "引言" in content
        or "中图法" in content
        or "文献标志码" in content
    ):
        return 2

    # 第3层：二维表格与日程数据 ，包含星期、打卡，或大量符号且平均行字数低
    if "星期一" in content and (
        "计划表" in content
        or "时间" in content
        or "打卡" in content
        or "X X" in content
    ):
        return 3

    table_symbols = sum(content.count(s) for s in ["X X", "√", "星期", "时间"])
    if avg_char_per_line < 15 and table_symbols > 5:
        return 3

    # 第4层：纯信息罗列清单 ，比如生字表，行数多、标点少、字数少
    if "生字表" in content or "识字表" in content:
        return 6

    chinese_punctuation = sum(
        content.count(p) for p in ["，", "。", "！", "？", "：", "；", "“", "”"]
    )
    if (
        line_count > 10
        and avg_char_per_line < 25
        and chinese_punctuation < line_count * 0.5
    ):
        return 6

    # 第5层：长篇结构化文档 vs 文学段落
    structured_patterns = [
        r"^一、",
        r"^（一）",
        r"^1\.",
        r"【篇",
        r"总结",
        r"第[一二三四五六七八九十]+部分",
    ]
    has_structure = any(
        re.search(p, content, re.MULTILINE) for p in structured_patterns
    )

    if char_count >= 500 and (
        has_structure or "工作总结" in content or "报告" in content
    ):
        return 1

    if char_count < 500 and not has_structure:
        return 5

    # 如果大于 500 字但没有明显序号，先暂时归类为长文，也可以视情况调整
    if char_count >= 500:
        return 1

    return 0


if not os.path.exists(source_dir):
    print(f"Error: 找不到源文件夹 {source_dir}")
    exit(1)

files = [f for f in os.listdir(source_dir) if f.endswith(".md")]

for f in files:
    file_path = os.path.join(source_dir, f)
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

        cat_id = classify(content, f)
        cat_name = categories[cat_id]

        # 统计指标记录
        char_count = len(content.strip())
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        line_count = len(lines)
        avg_char_per_line = round(char_count / line_count if line_count > 0 else 0, 2)

        results.append(
            {
                "file_name": f,
                "category_id": cat_id,
                "category_name": cat_name,
                "char_count": char_count,
                "line_count": line_count,
                "avg_char_per_line": avg_char_per_line,
            }
        )

        # 2. 复制文件到对应的子文件夹
        dest_path = os.path.join(dest_parent_dir, cat_name, f)
        shutil.copy2(file_path, dest_path)

    except Exception as e:
        print(f"处理文件 {f} 时出错: {e}")

# 3. 输出 CSV 统计表格
with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
    writer = csv.DictWriter(
        csvfile,
        fieldnames=[
            "file_name",
            "category_id",
            "category_name",
            "char_count",
            "line_count",
            "avg_char_per_line",
        ],
    )
    writer.writeheader()
    writer.writerows(results)

# 4. 打印统计概览
summary = {cat: 0 for cat in categories.values()}
for r in results:
    summary[r["category_name"]] += 1

print("\n🎉 分类完成！数据概览如下：")
print("=" * 40)
for cat, count in summary.items():
    print(f"- {cat:<25}: {count:>4} 篇")
print("=" * 40)
print(f"📂 所有分类文件已复制到 '{dest_parent_dir}/' 目录下。")
print(f"📊 详细统计信息已保存至 '{csv_path}'。")
