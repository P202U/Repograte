from dataclasses import dataclass
from typing import List, Optional
from e2b import Sandbox
from ..config import settings


@dataclass
class SandboxRunResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    step: str  # "clone" | "install" | "test" | "" (empty means it passed)


class E2BRunner:
    """Thin wrapper around one E2B sandbox lifecycle for one verification run."""

    def __init__(self, template: Optional[str] = None, timeout: Optional[int] = None):
        self.template = template or settings.e2b_template
        self.timeout = timeout or settings.e2b_timeout_seconds
        self.repo_dir = "/repo"

    def run_verification(
        self,
        repo_url: str,
        file_path: str,
        file_content: str,
        branch: Optional[str] = None,
        install_cmd: Optional[str] = None,
        test_cmd: Optional[str] = None,
        setup_cmds: Optional[List[str]] = None,
    ) -> SandboxRunResult:
        """
        Clones `repo_url`, overwrites `file_path` with `file_content`,
        then runs `install_cmd` and `test_cmd` from the repo root.

        `install_cmd`/`test_cmd` default to settings.sandbox_install_cmd /
        settings.sandbox_test_cmd (configurable via env vars or a per-run
        --install-cmd/--test-cmd CLI flag)
        """
        install_cmd = install_cmd or settings.sandbox_install_cmd
        test_cmd = test_cmd or settings.sandbox_test_cmd

        with Sandbox.create(
            template=self.template, timeout=self.timeout, api_key=settings.e2b_api_key
        ) as sbx:

            try:
                sbx.commands.run(f"mkdir -p {self.repo_dir}")

                if settings.github_token:
                    authenticated_url = repo_url.replace(
                        "https://", f"https://x-access-token:{settings.github_token}@"
                    )
                else:
                    authenticated_url = repo_url

                branch_flag = f"-b {branch}" if branch else ""
                clone_cmd = f"git clone --depth 1 {branch_flag} {authenticated_url} {self.repo_dir}"

                clone_res = sbx.commands.run(clone_cmd, timeout=self.timeout)
                if clone_res.exit_code != 0:
                    return SandboxRunResult(
                        success=False,
                        stdout=clone_res.stdout,
                        stderr=clone_res.stderr,
                        exit_code=clone_res.exit_code,
                        step="clone",
                    )
            except Exception as e:
                return SandboxRunResult(
                    success=False, stdout="", stderr=str(e), exit_code=1, step="clone"
                )

            for cmd in setup_cmds or []:
                setup_result = sbx.commands.run(
                    cmd, cwd=self.repo_dir, timeout=self.timeout
                )
                if setup_result.exit_code != 0:
                    return SandboxRunResult(
                        success=False,
                        stdout=setup_result.stdout,
                        stderr=setup_result.stderr,
                        exit_code=setup_result.exit_code,
                        step="setup",
                    )

            target_path = f"{self.repo_dir}/{file_path.lstrip('/')}"
            sbx.files.write(target_path, file_content)

            install_result = sbx.commands.run(
                install_cmd, cwd=self.repo_dir, timeout=self.timeout
            )
            if install_result.exit_code != 0:
                return SandboxRunResult(
                    success=False,
                    stdout=install_result.stdout,
                    stderr=install_result.stderr,
                    exit_code=install_result.exit_code,
                    step="install",
                )

            test_result = sbx.commands.run(
                test_cmd, cwd=self.repo_dir, timeout=self.timeout
            )
            return SandboxRunResult(
                success=test_result.exit_code == 0,
                stdout=test_result.stdout,
                stderr=test_result.stderr,
                exit_code=test_result.exit_code,
                step="" if test_result.exit_code == 0 else "test",
            )
