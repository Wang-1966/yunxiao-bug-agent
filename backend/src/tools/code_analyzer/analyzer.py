"""代码分析工具"""
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger
import tree_sitter_python as tspython
from tree_sitter import Language, Parser


class CodeAnalyzer:
    """代码分析工具"""

    def __init__(self, repo_path: str):
        """
        初始化代码分析器

        Args:
            repo_path: 仓库路径
        """
        self.repo_path = Path(repo_path)

        # 初始化 tree-sitter parser
        try:
            PY_LANGUAGE = Language(tspython.language())
            self.parser = Parser(PY_LANGUAGE)
        except Exception as e:
            logger.warning(f"Failed to initialize tree-sitter: {e}")
            self.parser = None

    def find_files_by_pattern(
        self,
        pattern: str,
        exclude_dirs: Optional[List[str]] = None
    ) -> List[str]:
        """
        查找匹配模式的文件

        Args:
            pattern: glob 模式
            exclude_dirs: 排除的目录列表

        Returns:
            文件路径列表
        """
        if exclude_dirs is None:
            exclude_dirs = [
                "node_modules", ".git", "__pycache__",
                "venv", ".venv", "dist", "build"
            ]

        files = []
        for path in self.repo_path.rglob(pattern):
            if path.is_file():
                # 检查是否在排除目录中
                if not any(exc in path.parts for exc in exclude_dirs):
                    files.append(str(path.relative_to(self.repo_path)))

        return files

    def analyze_python_file(self, file_path: str) -> Dict[str, Any]:
        """
        分析 Python 文件结构

        Args:
            file_path: 文件路径（相对于仓库根目录）

        Returns:
            文件结构信息
        """
        if not self.parser:
            logger.warning("Parser not available")
            return {}

        try:
            full_path = self.repo_path / file_path
            with open(full_path, "rb") as f:
                code = f.read()

            tree = self.parser.parse(code)
            root_node = tree.root_node

            # 提取函数和类
            functions = []
            classes = []

            def traverse(node):
                if node.type == "function_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        functions.append({
                            "name": name_node.text.decode(),
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1
                        })

                elif node.type == "class_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        classes.append({
                            "name": name_node.text.decode(),
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1
                        })

                for child in node.children:
                    traverse(child)

            traverse(root_node)

            return {
                "file": file_path,
                "functions": functions,
                "classes": classes
            }

        except Exception as e:
            logger.error(f"Failed to analyze file {file_path}: {e}")
            return {"file": file_path, "error": str(e)}

    def search_in_files(
        self,
        keyword: str,
        file_pattern: str = "*.py"
    ) -> List[Dict[str, Any]]:
        """
        在文件中搜索关键字

        Args:
            keyword: 搜索关键字
            file_pattern: 文件模式

        Returns:
            搜索结果列表
        """
        results = []
        files = self.find_files_by_pattern(file_pattern)

        for file_path in files:
            try:
                full_path = self.repo_path / file_path
                with open(full_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                for i, line in enumerate(lines, 1):
                    if keyword.lower() in line.lower():
                        results.append({
                            "file": file_path,
                            "line": i,
                            "content": line.strip()
                        })

            except Exception as e:
                logger.error(f"Failed to search in {file_path}: {e}")

        return results

    def get_file_content(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None
    ) -> str:
        """
        获取文件内容

        Args:
            file_path: 文件路径
            start_line: 起始行（从 1 开始）
            end_line: 结束行

        Returns:
            文件内容
        """
        try:
            full_path = self.repo_path / file_path
            with open(full_path, "r", encoding="utf-8") as f:
                if start_line is None and end_line is None:
                    return f.read()

                lines = f.readlines()
                start = (start_line or 1) - 1
                end = end_line if end_line else len(lines)
                return "".join(lines[start:end])

        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return ""

    def get_dependencies(self, file_path: str) -> List[str]:
        """
        获取文件的依赖（import 语句）

        Args:
            file_path: 文件路径

        Returns:
            依赖列表
        """
        try:
            content = self.get_file_content(file_path)
            imports = []

            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("import ") or line.startswith("from "):
                    imports.append(line)

            return imports

        except Exception as e:
            logger.error(f"Failed to get dependencies for {file_path}: {e}")
            return []
