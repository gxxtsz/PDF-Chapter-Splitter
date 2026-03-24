"""PDF Chapter Splitter - PySide6 图形界面"""

import os
import re
import sys
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from main import (
    DEFAULT_BOOK_PATTERNS,
    DEFAULT_CHAPTER_PATTERNS,
    DEFAULT_SKIP_PATTERNS,
    DEFAULT_VOLUME_PATTERNS,
    split_pdf,
)


# ── 工作线程 ──────────────────────────────────────────────────


class SplitWorker(QThread):
    """在后台线程执行 PDF 拆分，避免阻塞 UI"""

    log_signal = Signal(str)
    progress_signal = Signal(int, int)  # current, total
    finished_signal = Signal(str)       # output_dir
    error_signal = Signal(str)

    def __init__(
        self,
        pdf_path: str,
        output_dir: str | None,
        book_patterns: list[str] | None = None,
        volume_patterns: list[str] | None = None,
        chapter_patterns: list[str] | None = None,
        skip_patterns: list[str] | None = None,
    ):
        super().__init__()
        self.pdf_path = pdf_path
        self.output_dir = output_dir
        self.book_patterns = book_patterns
        self.volume_patterns = volume_patterns
        self.chapter_patterns = chapter_patterns
        self.skip_patterns = skip_patterns

    def run(self):
        try:
            result = split_pdf(
                self.pdf_path,
                self.output_dir if self.output_dir else None,
                log=self.log_signal.emit,
                on_progress=lambda cur, tot: self.progress_signal.emit(cur, tot),
                book_patterns=self.book_patterns,
                volume_patterns=self.volume_patterns,
                chapter_patterns=self.chapter_patterns,
                skip_patterns=self.skip_patterns,
            )
            self.finished_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")


# ── 主窗口 ────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Chapter Splitter")
        self.setMinimumSize(750, 800)
        self.worker: SplitWorker | None = None
        self._init_ui()

    # ── 界面搭建 ──

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- PDF 文件选择 ---
        layout.addWidget(QLabel("PDF 文件路径："))
        row_pdf = QHBoxLayout()
        self.pdf_input = QLineEdit()
        self.pdf_input.setPlaceholderText(
            "拖拽 PDF 文件到此窗口, 或点击浏览按钮选择文件"
        )
        row_pdf.addWidget(self.pdf_input)
        btn_browse_pdf = QPushButton("浏览…")
        btn_browse_pdf.setFixedWidth(80)
        btn_browse_pdf.clicked.connect(self._browse_pdf)
        row_pdf.addWidget(btn_browse_pdf)
        layout.addLayout(row_pdf)

        # --- 输出目录选择 ---
        layout.addWidget(QLabel("输出目录（可选，留空则自动生成）："))
        row_out = QHBoxLayout()
        self.out_input = QLineEdit()
        self.out_input.setPlaceholderText("默认在 PDF 同目录下创建 <PDF名>_split 文件夹")
        row_out.addWidget(self.out_input)
        btn_browse_out = QPushButton("浏览…")
        btn_browse_out.setFixedWidth(80)
        btn_browse_out.clicked.connect(self._browse_output)
        row_out.addWidget(btn_browse_out)
        layout.addLayout(row_out)

        # --- 匹配规则配置 ---
        patterns_group = QGroupBox("匹配规则配置（正则表达式，每行一条）")
        patterns_layout = QVBoxLayout(patterns_group)
        patterns_layout.setSpacing(6)

        pat_font = QFont("Consolas", 9)

        # 书籍级
        patterns_layout.addWidget(QLabel("书籍级关键词："))
        self.book_pat_edit = QTextEdit()
        self.book_pat_edit.setFont(pat_font)
        self.book_pat_edit.setFixedHeight(48)
        self.book_pat_edit.setPlainText("\n".join(DEFAULT_BOOK_PATTERNS))
        patterns_layout.addWidget(self.book_pat_edit)

        # 卷级
        patterns_layout.addWidget(QLabel("卷级关键词："))
        self.volume_pat_edit = QTextEdit()
        self.volume_pat_edit.setFont(pat_font)
        self.volume_pat_edit.setFixedHeight(48)
        self.volume_pat_edit.setPlainText("\n".join(DEFAULT_VOLUME_PATTERNS))
        patterns_layout.addWidget(self.volume_pat_edit)

        # 章级
        patterns_layout.addWidget(QLabel("章级关键词："))
        self.chapter_pat_edit = QTextEdit()
        self.chapter_pat_edit.setFont(pat_font)
        self.chapter_pat_edit.setFixedHeight(48)
        self.chapter_pat_edit.setPlainText("\n".join(DEFAULT_CHAPTER_PATTERNS))
        patterns_layout.addWidget(self.chapter_pat_edit)

        # 跳过
        patterns_layout.addWidget(QLabel("跳过关键词："))
        self.skip_pat_edit = QTextEdit()
        self.skip_pat_edit.setFont(pat_font)
        self.skip_pat_edit.setFixedHeight(48)
        self.skip_pat_edit.setPlainText("\n".join(DEFAULT_SKIP_PATTERNS))
        patterns_layout.addWidget(self.skip_pat_edit)

        layout.addWidget(patterns_group)

        # --- 操作按钮 ---
        btn_row = QHBoxLayout()
        self.btn_split = QPushButton("开始拆分")
        self.btn_split.setFixedHeight(36)
        self.btn_split.clicked.connect(self._start_split)
        btn_row.addWidget(self.btn_split)

        self.btn_open_dir = QPushButton("打开输出目录")
        self.btn_open_dir.setFixedHeight(36)
        self.btn_open_dir.setEnabled(False)
        self.btn_open_dir.clicked.connect(self._open_output_dir)
        btn_row.addWidget(self.btn_open_dir)
        layout.addLayout(btn_row)

        # --- 进度条 ---
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # --- 日志区域 ---
        layout.addWidget(QLabel("运行日志："))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_area, stretch=1)

        # 支持拖拽
        self.setAcceptDrops(True)

        self._output_dir_result: str = ""

    # ── 文件浏览 ──

    def _browse_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if path:
            self.pdf_input.setText(path)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.out_input.setText(path)

    # ── 拖拽支持 ──

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".pdf"):
                self.pdf_input.setText(path)

    # ── 辅助方法 ──

    @staticmethod
    def _parse_patterns(text: str) -> list[str]:
        """将多行文本解析为非空的正则字符串列表"""
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _validate_patterns(self) -> tuple[list[str], list[str], list[str], list[str]] | None:
        """校验所有正则输入，返回 (book, volume, chapter, skip) 或 None（出错时）"""
        fields = [
            ("书籍级", self.book_pat_edit),
            ("卷级", self.volume_pat_edit),
            ("章级", self.chapter_pat_edit),
            ("跳过", self.skip_pat_edit),
        ]
        results: list[list[str]] = []
        for label, edit in fields:
            patterns = self._parse_patterns(edit.toPlainText())
            for p in patterns:
                try:
                    re.compile(p)
                except re.error as e:
                    QMessageBox.warning(
                        self, "正则表达式错误",
                        f"{label}规则中的正则无效：\n{p}\n\n错误：{e}",
                    )
                    edit.setFocus()
                    return None
            results.append(patterns)
        return tuple(results)  # type: ignore[return-value]

    # ── 拆分逻辑 ──

    def _start_split(self):
        pdf_path = self.pdf_input.text().strip()
        if not pdf_path:
            QMessageBox.warning(self, "提示", "请先选择 PDF 文件。")
            return
        if not os.path.isfile(pdf_path):
            QMessageBox.warning(self, "提示", f"文件不存在：\n{pdf_path}")
            return

        # 校验正则
        pat_result = self._validate_patterns()
        if pat_result is None:
            return
        book_pats, volume_pats, chapter_pats, skip_pats = pat_result

        output_dir = self.out_input.text().strip() or None

        # 重置 UI
        self.log_area.clear()
        self.progress.setValue(0)
        self.btn_split.setEnabled(False)
        self.btn_open_dir.setEnabled(False)

        # 启动后台线程
        self.worker = SplitWorker(
            pdf_path,
            output_dir,
            book_patterns=book_pats or None,
            volume_patterns=volume_pats or None,
            chapter_patterns=chapter_pats or None,
            skip_patterns=skip_pats or None,
        )
        self.worker.log_signal.connect(self._append_log)
        self.worker.progress_signal.connect(self._update_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.error_signal.connect(self._on_error)
        self.worker.start()

    def _append_log(self, msg: str):
        self.log_area.append(msg)

    def _update_progress(self, current: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(current)

    def _on_finished(self, output_dir: str):
        self._output_dir_result = output_dir
        self.btn_split.setEnabled(True)
        self.btn_open_dir.setEnabled(True)

    def _on_error(self, msg: str):
        self.btn_split.setEnabled(True)
        self._append_log(f"\n错误：{msg}")
        QMessageBox.critical(self, "错误", f"拆分失败：\n{msg.splitlines()[0]}")

    def _open_output_dir(self):
        if self._output_dir_result and os.path.isdir(self._output_dir_result):
            os.startfile(self._output_dir_result)


# ── 入口 ──────────────────────────────────────────────────────


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
