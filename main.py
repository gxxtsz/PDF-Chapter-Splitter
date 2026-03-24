"""PDF Chapter Splitter - 按书籍/卷/章拆分PDF工具

根据PDF书签(目录)信息，智能识别书籍、卷、章层级，
将PDF按章拆分为多个小文件。
"""

import re
import sys
import os
from pathlib import Path

import fitz  # PyMuPDF


# ── 层级分类规则（默认值） ────────────────────────────────────

# 书籍级关键词
DEFAULT_BOOK_PATTERNS = [
    r"第[一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾\d]+[册部]",
]

# 卷级关键词
DEFAULT_VOLUME_PATTERNS = [
    r"第[一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾\d]+[卷篇]",
]

# 章级关键词
DEFAULT_CHAPTER_PATTERNS = [
    r"第[一二三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾\d]+[章回节讲课话]",
]

# 需要跳过的条目关键词（优先判断，即使匹配了书/卷/章也跳过）
DEFAULT_SKIP_PATTERNS = [
    r"附录",
]


def compile_patterns(raw: list[str]) -> list[re.Pattern]:
    """将字符串列表编译为正则表达式列表，跳过空字符串"""
    compiled = []
    for s in raw:
        s = s.strip()
        if s:
            compiled.append(re.compile(s))
    return compiled


class BookmarkNode:
    """表示一个书签节点及其分类信息"""

    def __init__(self, level: int, title: str, page: int):
        self.level = level          # 书签的原始层级 (1-based)
        self.title = title.strip()  # 书签标题
        self.page = page            # 起始页码 (0-based)
        self.category = ""          # book / volume / chapter / unknown
        self.book_name = ""         # 所属书籍名
        self.volume_name = ""       # 所属卷名

    def __repr__(self):
        return (f"BookmarkNode(level={self.level}, title={self.title!r}, "
                f"page={self.page}, cat={self.category})")


def classify_title(
    title: str,
    book_pats: list[re.Pattern],
    volume_pats: list[re.Pattern],
    chapter_pats: list[re.Pattern],
    skip_pats: list[re.Pattern],
) -> str:
    """根据标题内容判断属于 book / volume / chapter / skip / unknown"""
    for pat in skip_pats:
        if pat.search(title):
            return "skip"
    for pat in book_pats:
        if pat.search(title):
            return "book"
    for pat in volume_pats:
        if pat.search(title):
            return "volume"
    for pat in chapter_pats:
        if pat.search(title):
            return "chapter"
    return "skip"


def classify_by_level(
    nodes: list[BookmarkNode],
    book_pats: list[re.Pattern] | None = None,
    volume_pats: list[re.Pattern] | None = None,
    chapter_pats: list[re.Pattern] | None = None,
    skip_pats: list[re.Pattern] | None = None,
) -> None:
    """基于书签层级进行分类（作为关键词分类的补充）

    策略：
    - 先用关键词分类
    - 如果关键词完全没命中，则按层级映射：
      level 1 → book，level 2 → volume，level 3+ → chapter
    - 如果仅有部分命中，用层级信息辅助填充 unknown 节点
    """
    if book_pats is None:
        book_pats = compile_patterns(DEFAULT_BOOK_PATTERNS)
    if volume_pats is None:
        volume_pats = compile_patterns(DEFAULT_VOLUME_PATTERNS)
    if chapter_pats is None:
        chapter_pats = compile_patterns(DEFAULT_CHAPTER_PATTERNS)
    if skip_pats is None:
        skip_pats = compile_patterns(DEFAULT_SKIP_PATTERNS)

    # 第一轮：关键词分类
    for node in nodes:
        node.category = classify_title(
            node.title, book_pats, volume_pats, chapter_pats, skip_pats
        )

    # 第二轮：将章的子标签标记为 ignore（完全忽略，不参与边界计算）
    in_chapter = False
    chapter_level = 0
    for node in nodes:
        if node.category == "chapter":
            in_chapter = True
            chapter_level = node.level
        elif node.category in ("book", "volume"):
            in_chapter = False
        elif in_chapter and node.level > chapter_level:
            # 这是章的子标签，完全忽略
            node.category = "ignore"

    # 统计有多少通过关键词分类成功的
    classified = {n.category for n in nodes} - {"skip", "ignore"}

    if not classified:
        # 关键词完全没命中 → 纯靠层级
        levels = sorted(set(n.level for n in nodes))
        level_map = {}
        categories = ["book", "volume", "chapter"]
        for i, lv in enumerate(levels):
            if i < len(categories):
                level_map[lv] = categories[i]
            else:
                level_map[lv] = "chapter"
        # 如果只有一个层级，全部视为 chapter
        if len(levels) == 1:
            for n in nodes:
                n.category = "chapter"
        else:
            for n in nodes:
                n.category = level_map[n.level]
    else:
        # 部分命中 → 推断 unknown 节点
        # 收集每种类别对应的层级
        cat_levels: dict[str, set[int]] = {}
        for n in nodes:
            if n.category != "unknown":
                cat_levels.setdefault(n.category, set()).add(n.level)

        for n in nodes:
            if n.category == "unknown":
                # 按层级推断：和已知类别的层级比较
                assigned = False
                for cat in ["book", "volume", "chapter"]:
                    if cat in cat_levels and n.level in cat_levels[cat]:
                        n.category = cat
                        assigned = True
                        break
                if not assigned:
                    # 层级没有直接匹配，按与已知类别层级的关系推断
                    if "chapter" in cat_levels:
                        chapter_levels = cat_levels["chapter"]
                        if n.level >= min(chapter_levels):
                            n.category = "chapter"
                            assigned = True
                    if not assigned:
                        if "volume" in cat_levels:
                            volume_levels = cat_levels["volume"]
                            if n.level >= min(volume_levels):
                                n.category = "volume"
                                assigned = True
                    if not assigned:
                        # 默认归为 chapter
                        n.category = "chapter"


def build_hierarchy(nodes: list[BookmarkNode]) -> None:
    """为每个节点确定所属的 book_name 和 volume_name"""
    current_book = ""
    current_volume = ""

    for node in nodes:
        if node.category == "book":
            current_book = node.title
            current_volume = ""  # 换书时重置卷
        elif node.category == "volume":
            current_volume = node.title
        # 记录归属
        node.book_name = current_book
        node.volume_name = current_volume


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    # 替换 Windows 不允许的文件名字符
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # 去除首尾空格和点
    name = name.strip(" .")
    # 压缩连续空格
    name = re.sub(r'\s+', ' ', name)
    return name


def extract_bookmarks(doc: fitz.Document) -> list[BookmarkNode]:
    """从PDF文档提取书签列表"""
    toc = doc.get_toc(simple=True)  # [(level, title, page), ...]
    nodes = []
    for level, title, page in toc:
        # page 是 1-based，转为 0-based
        nodes.append(BookmarkNode(level, title, page - 1))
    return nodes


def get_chapter_ranges(
    nodes: list[BookmarkNode], total_pages: int
) -> list[tuple[BookmarkNode, int, int]]:
    """计算每个章节的页码范围 (start, end)，end 是包含的最后一页

    拆分粒度是 chapter。章的结束页 = 下一个 book/volume/chapter 的起始页 - 1。
    """
    # 筛选出所有 chapter 节点（skip 节点不拆分，但其页码参与边界计算）
    chapters = [n for n in nodes if n.category == "chapter"]
    if not chapters:
        print("警告：未找到任何章节级书签，将尝试把所有书签作为章节处理。")
        chapters = [n for n in nodes if n.category != "skip"]

    # 构建「所有有意义节点」的起始页列表，用来确定结束页（排除 ignore 节点）
    all_start_pages = sorted(set(n.page for n in nodes if n.category != "ignore"))

    result = []
    for i, ch in enumerate(chapters):
        start = ch.page
        # 找比当前章节起始页大的下一个起始页
        end = total_pages - 1  # 默认到文档末尾
        for sp in all_start_pages:
            if sp > start:
                end = sp - 1
                break
        # 如果不是最后一个 chapter，也需要考虑下一个 chapter 的起始页
        if i + 1 < len(chapters):
            next_start = chapters[i + 1].page
            if next_start - 1 < end:
                end = next_start - 1

        # 确保 end >= start
        if end < start:
            end = start

        result.append((ch, start, end))

    return result


def split_pdf(
    pdf_path: str,
    output_dir: str | None = None,
    log=None,
    on_progress=None,
    book_patterns: list[str] | None = None,
    volume_patterns: list[str] | None = None,
    chapter_patterns: list[str] | None = None,
    skip_patterns: list[str] | None = None,
) -> str:
    """主入口：拆分PDF文件

    Args:
        pdf_path: PDF文件路径
        output_dir: 输出目录，默认为PDF同目录下的同名文件夹
        log: 可选的日志回调函数 log(msg: str)
        on_progress: 可选的进度回调 on_progress(current: int, total: int)
        book_patterns: 自定义书籍级正则列表（原始字符串）
        volume_patterns: 自定义卷级正则列表
        chapter_patterns: 自定义章级正则列表
        skip_patterns: 自定义跳过正则列表

    Returns:
        输出目录路径
    """
    def _log(msg: str):
        if log:
            log(msg)
        else:
            print(msg)

    pdf_path = os.path.abspath(pdf_path)
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"文件不存在 - {pdf_path}")

    pdf_name = Path(pdf_path).stem
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(pdf_path), pdf_name + "_split")
    if os.path.isdir(output_dir):
        import shutil
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    _log(f"打开PDF: {pdf_path}")
    _log(f"总页数: {total_pages}")

    # 1. 提取书签
    nodes = extract_bookmarks(doc)
    if not nodes:
        doc.close()
        raise ValueError("该PDF没有书签信息，无法拆分。")

    _log(f"提取到 {len(nodes)} 个书签")

    # 2. 分类（使用自定义或默认 patterns）
    book_pats = compile_patterns(book_patterns) if book_patterns else None
    volume_pats = compile_patterns(volume_patterns) if volume_patterns else None
    chapter_pats = compile_patterns(chapter_patterns) if chapter_patterns else None
    skip_pats = compile_patterns(skip_patterns) if skip_patterns else None
    classify_by_level(nodes, book_pats, volume_pats, chapter_pats, skip_pats)

    # 3. 构建层级关系
    build_hierarchy(nodes)

    # 打印书签结构
    _log("\n书签结构：")
    for n in nodes:
        indent = "  " * (n.level - 1)
        _log(f"  {indent}[{n.category:7s}] {n.title} (p.{n.page + 1})")

    # 4. 计算章节范围
    chapter_ranges = get_chapter_ranges(nodes, total_pages)
    total_chapters = len(chapter_ranges)
    _log(f"\n共 {total_chapters} 个章节将被拆分\n")

    # 5. 拆分并保存
    for idx, (ch, start, end) in enumerate(chapter_ranges, 1):
        page_count = end - start + 1
        page_info = f"p.{start + 1}-{end + 1}({page_count}页)"
        # 构建文件名：序号-书籍名-卷名-章节名-页码信息
        parts = [f"{idx:03d}"]
        if ch.book_name:
            parts.append(sanitize_filename(ch.book_name))
        if ch.volume_name:
            parts.append(sanitize_filename(ch.volume_name))
        parts.append(sanitize_filename(ch.title))
        parts.append(page_info)

        filename = "-".join(parts) + ".pdf"
        out_path = os.path.join(output_dir, filename)

        # 提取页面范围
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=start, to_page=end)
        new_doc.save(out_path)
        new_doc.close()

        _log(f"  [{idx:03d}] p.{start + 1}-{end + 1} ({page_count}页) → {filename}")
        if on_progress:
            on_progress(idx, total_chapters)

    doc.close()
    _log(f"\n拆分完成！共生成 {total_chapters} 个文件")
    _log(f"输出目录: {output_dir}")
    return output_dir


def main():
    if len(sys.argv) < 2:
        print("用法: uv run main.py <PDF文件路径> [输出目录]")
        print("\n示例:")
        print("  uv run main.py book.pdf")
        print("  uv run main.py book.pdf ./output")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    split_pdf(pdf_path, output_dir)


if __name__ == "__main__":
    main()
