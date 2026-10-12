"""Git 操作工具"""
import os
import git
from pathlib import Path
from typing import Optional
from loguru import logger

from ...config import get_settings

settings = get_settings()


class GitOperator:
    """Git 操作封装"""

    def __init__(self, repo_path: str):
        """
        初始化 Git 操作器

        Args:
            repo_path: 仓库路径
        """
        self.repo_path = repo_path
        self.repo = git.Repo(repo_path)

    def create_worktree(self, bug_id: str) -> str:
        """
        创建独立的 worktree

        Args:
            bug_id: Bug ID

        Returns:
            worktree 路径
        """
        try:
            worktree_name = f"bug-{bug_id}"
            worktree_path = os.path.join(
                self.repo_path,
                settings.worktrees_dir,
                worktree_name
            )

            # 创建新分支
            branch_name = f"fix/{bug_id}"

            # 如果 worktree 已存在，先删除
            if os.path.exists(worktree_path):
                self.remove_worktree(bug_id)

            # 创建 worktree
            self.repo.git.worktree("add", worktree_path, "-b", branch_name)

            logger.info(f"Worktree created: {worktree_path}")
            return worktree_path

        except Exception as e:
            logger.error(f"Failed to create worktree: {e}")
            raise

    def remove_worktree(self, bug_id: str):
        """
        删除 worktree

        Args:
            bug_id: Bug ID
        """
        try:
            worktree_name = f"bug-{bug_id}"
            worktree_path = os.path.join(
                self.repo_path,
                settings.worktrees_dir,
                worktree_name
            )

            if os.path.exists(worktree_path):
                self.repo.git.worktree("remove", worktree_path, "--force")
                logger.info(f"Worktree removed: {worktree_path}")

        except Exception as e:
            logger.error(f"Failed to remove worktree: {e}")
            raise

    def commit_changes(
        self,
        worktree_path: str,
        message: str,
        files: Optional[list[str]] = None
    ) -> str:
        """
        在 worktree 中提交更改

        Args:
            worktree_path: worktree 路径
            message: 提交消息
            files: 要提交的文件列表，None 表示所有修改

        Returns:
            提交哈希
        """
        try:
            worktree_repo = git.Repo(worktree_path)

            # 添加文件
            if files:
                worktree_repo.index.add(files)
            else:
                worktree_repo.git.add(A=True)

            # 提交
            commit = worktree_repo.index.commit(message)

            logger.info(f"Changes committed: {commit.hexsha[:8]}")
            return commit.hexsha

        except Exception as e:
            logger.error(f"Failed to commit changes: {e}")
            raise

    def get_diff(self, worktree_path: str) -> str:
        """
        获取 worktree 的 diff

        Args:
            worktree_path: worktree 路径

        Returns:
            diff 内容
        """
        try:
            worktree_repo = git.Repo(worktree_path)
            return worktree_repo.git.diff("HEAD")

        except Exception as e:
            logger.error(f"Failed to get diff: {e}")
            return ""

    def create_pr_branch(self, worktree_path: str, base_branch: str = "main"):
        """
        从 worktree 创建 PR 分支

        Args:
            worktree_path: worktree 路径
            base_branch: 基础分支
        """
        try:
            worktree_repo = git.Repo(worktree_path)

            # 推送到远程
            origin = worktree_repo.remote("origin")
            current_branch = worktree_repo.active_branch
            origin.push(current_branch)

            logger.info(f"Branch pushed: {current_branch.name}")

        except Exception as e:
            logger.error(f"Failed to push branch: {e}")
            raise
