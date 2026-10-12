"""测试运行器"""
import subprocess
import json
from typing import Dict, Any, Optional
from pathlib import Path
from loguru import logger


class TestRunner:
    """测试运行工具"""

    def __init__(self, repo_path: str):
        """
        初始化测试运行器

        Args:
            repo_path: 仓库路径
        """
        self.repo_path = Path(repo_path)

    def detect_test_framework(self) -> Optional[str]:
        """
        检测测试框架

        Returns:
            测试框架名称 (pytest, jest, maven, gradle, etc.)
        """
        # Python
        if (self.repo_path / "pytest.ini").exists() or \
           (self.repo_path / "setup.py").exists():
            return "pytest"

        # JavaScript/TypeScript
        if (self.repo_path / "package.json").exists():
            try:
                with open(self.repo_path / "package.json") as f:
                    pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "jest" in deps:
                        return "jest"
                    if "mocha" in deps:
                        return "mocha"
            except Exception:
                pass

        # Java
        if (self.repo_path / "pom.xml").exists():
            return "maven"
        if (self.repo_path / "build.gradle").exists():
            return "gradle"

        # Go
        if any(self.repo_path.glob("*_test.go")):
            return "go"

        return None

    async def run_tests(
        self,
        test_path: Optional[str] = None,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """
        运行测试

        Args:
            test_path: 特定测试文件或目录，None 表示运行所有测试
            timeout: 超时时间（秒）

        Returns:
            测试结果
        """
        framework = self.detect_test_framework()
        if not framework:
            logger.warning("No test framework detected")
            return {
                "success": False,
                "message": "No test framework detected",
                "total": 0,
                "passed": 0,
                "failed": 0
            }

        logger.info(f"Running tests with {framework}")

        try:
            if framework == "pytest":
                return await self._run_pytest(test_path, timeout)
            elif framework == "jest":
                return await self._run_jest(test_path, timeout)
            elif framework == "maven":
                return await self._run_maven(timeout)
            elif framework == "gradle":
                return await self._run_gradle(timeout)
            elif framework == "go":
                return await self._run_go(test_path, timeout)
            else:
                return {
                    "success": False,
                    "message": f"Framework {framework} not supported yet",
                    "total": 0,
                    "passed": 0,
                    "failed": 0
                }

        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            return {
                "success": False,
                "message": str(e),
                "total": 0,
                "passed": 0,
                "failed": 0
            }

    async def _run_pytest(self, test_path: Optional[str], timeout: int) -> Dict[str, Any]:
        """运行 pytest"""
        cmd = ["pytest", "-v", "--tb=short"]
        if test_path:
            cmd.append(test_path)

        result = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # 解析输出
        output = result.stdout + result.stderr
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")

        return {
            "success": result.returncode == 0,
            "message": "Tests completed",
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "output": output
        }

    async def _run_jest(self, test_path: Optional[str], timeout: int) -> Dict[str, Any]:
        """运行 jest"""
        cmd = ["npm", "test", "--", "--json"]
        if test_path:
            cmd.append(test_path)

        result = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # 尝试解析 JSON 输出
        try:
            test_results = json.loads(result.stdout)
            return {
                "success": test_results.get("success", False),
                "message": "Tests completed",
                "total": test_results.get("numTotalTests", 0),
                "passed": test_results.get("numPassedTests", 0),
                "failed": test_results.get("numFailedTests", 0),
                "output": result.stdout
            }
        except json.JSONDecodeError:
            return {
                "success": result.returncode == 0,
                "message": "Tests completed (unable to parse JSON)",
                "total": 0,
                "passed": 0,
                "failed": 0,
                "output": result.stdout + result.stderr
            }

    async def _run_maven(self, timeout: int) -> Dict[str, Any]:
        """运行 Maven 测试"""
        result = subprocess.run(
            ["mvn", "test"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        output = result.stdout + result.stderr
        # 简单解析（实际需要更复杂的解析）
        return {
            "success": result.returncode == 0,
            "message": "Tests completed",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "output": output
        }

    async def _run_gradle(self, timeout: int) -> Dict[str, Any]:
        """运行 Gradle 测试"""
        result = subprocess.run(
            ["./gradlew", "test"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        output = result.stdout + result.stderr
        return {
            "success": result.returncode == 0,
            "message": "Tests completed",
            "total": 0,
            "passed": 0,
            "failed": 0,
            "output": output
        }

    async def _run_go(self, test_path: Optional[str], timeout: int) -> Dict[str, Any]:
        """运行 Go 测试"""
        cmd = ["go", "test", "-v"]
        if test_path:
            cmd.append(test_path)
        else:
            cmd.append("./...")

        result = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        output = result.stdout + result.stderr
        passed = output.count("--- PASS:")
        failed = output.count("--- FAIL:")

        return {
            "success": result.returncode == 0,
            "message": "Tests completed",
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "output": output
        }
