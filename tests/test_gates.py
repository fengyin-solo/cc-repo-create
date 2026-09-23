"""技能自带的自测：先真实生成一个仓库，再逐条把闸门拆掉，确认闸门真的会拦。

跑法：

    python3 -m unittest discover -s ~/.kunlunxiaozhi/skills/cc-repo-create/tests -v

只做生成与静态验收，不装依赖、不起服务（那两步由 scripts/probe_startup.py 负责）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import repo_rules as rules  # noqa: E402
import publish_github as publisher  # noqa: E402
import scaffold_repo as scaffold_mod  # noqa: E402
import verify_repo as verifier  # noqa: E402

SPEC = SKILL_DIR / "examples" / "spec-park-pass.json"


def publish_args(**overrides) -> argparse.Namespace:
    defaults = {
        "repo": "", "branch": "main", "owner": None, "name": None, "visibility": "public",
        "description": None, "remote_url": None, "skip_verify": False, "allow_dirty": False,
        "execute": False, "confirm": None, "json": True,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def generate(target: Path, *extra: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "scaffold_repo.py"), "--out", str(target), *extra],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"生成失败：{result.stderr}")
    return json.loads(result.stdout)


class ScaffoldAndVerify(unittest.TestCase):
    tmp: Path
    repo: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="cc-repo-create-test-"))
        cls.repo = cls.tmp / "app"
        generate(cls.repo, "--git-init")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verify(self, repo: Path | None = None, **kwargs) -> dict:
        return verifier.verify(repo or self.repo, min_lines=kwargs.pop("min_lines", 2000),
                               min_capacity=kwargs.pop("min_capacity", 30),
                               per_mode=kwargs.pop("per_mode", rules.MAX_PER_MODE),
                               per_subject=kwargs.pop("per_subject", rules.MAX_PER_SUBJECT))

    def test_generated_repo_passes_all_gates(self):
        result = self.verify()
        self.assertTrue(result["ok"], msg=result["failures"])
        self.assertGreaterEqual(result["checks"]["code_size"]["code_lines"], 2000)
        self.assertGreaterEqual(result["checks"]["capacity"]["capacity"], 30)
        self.assertEqual(result["checks"]["structure"]["frontend_stack"], "vue")
        self.assertEqual(result["checks"]["structure"]["backend_stack"], "python")

    def test_capacity_matches_subject_math(self):
        subjects = rules.derive_subjects(self.repo)
        capacity = rules.compute_capacity(subjects)
        expected = min(rules.MAX_PER_REPO,
                       len(subjects) * rules.MAX_PER_SUBJECT,
                       len(rules.USABLE_MODES) * rules.MAX_PER_MODE)
        self.assertEqual(capacity["capacity"], expected)
        self.assertGreaterEqual(len(subjects), 16,
                                "主体数少于 16 时容量撑不到 30 条")

    def test_rule_snapshot_matches_generated_repo_derivation(self):
        """同一批源码，主体派生必须是稳定的；改规则会立刻在这里暴露。"""
        first = rules.derive_subjects(self.repo)
        second = rules.derive_subjects(self.repo)
        self.assertEqual(set(first), set(second))
        self.assertIn("visitor", first)

    def test_auto_open_true_is_rejected(self):
        copy = self.tmp / "auto-open"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        vite = copy / "frontend" / "vite.config.ts"
        vite.write_text(vite.read_text(encoding="utf-8").replace("open: false", "open: true"),
                        encoding="utf-8")
        result = self.verify(copy)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["auto_open"]["ok"])

    def test_missing_gitignore_is_rejected(self):
        copy = self.tmp / "no-gitignore"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / ".gitignore").unlink()
        result = self.verify(copy)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["gitignore"]["ok"])

    def test_small_repo_fails_size_gate(self):
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        spec["modules"] = spec["modules"][:3]
        small_spec = self.tmp / "small-spec.json"
        small_spec.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        target = self.tmp / "small"
        generate(target, "--spec", str(small_spec))
        result = self.verify(target)
        self.assertFalse(result["ok"])
        self.assertTrue(any("G4 代码量不足" in failure for failure in result["failures"]),
                        msg=result["failures"])

    def test_capacity_gate_rejects_few_subjects(self):
        """主体不够时容量必然够不到 30 条，闸门要拦下。"""
        target = self.tmp / "thin"
        (target / "frontend" / "src" / "views" / "alpha").mkdir(parents=True)
        (target / "frontend" / "src" / "views" / "beta").mkdir(parents=True)
        for name in ("alpha", "beta"):
            (target / "frontend" / "src" / "views" / name / "index.vue").write_text(
                "<template><div>工单列表</div></template>\n", encoding="utf-8")
        result = verifier.check_capacity(target, min_capacity=30, per_mode=rules.MAX_PER_MODE,
                                         per_subject=rules.MAX_PER_SUBJECT)
        self.assertFalse(result["ok"])
        self.assertLess(result["capacity"], 30)

    def test_hardcoded_secret_is_rejected(self):
        copy = self.tmp / "secret"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        target = copy / "backend" / "app" / "config.py"
        target.write_text(target.read_text(encoding="utf-8")
                          + '\nAPI_TOKEN = "sk-abcdefghijklmnopqrstuvwx1234"\n', encoding="utf-8")
        result = self.verify(copy)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["compliance"]["ok"])

    def test_dependency_dirs_are_ignored_by_git(self):
        """依赖目录一旦落地，必须被 .gitignore 挡住，且不进 git 待提交列表。"""
        copy = self.tmp / "deps"
        shutil.copytree(self.repo, copy)
        (copy / "frontend" / "node_modules" / "vue").mkdir(parents=True)
        (copy / "frontend" / "node_modules" / "vue" / "index.js").write_text("export {}\n",
                                                                             encoding="utf-8")
        (copy / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
        (copy / "frontend" / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        result = self.verify(copy)
        self.assertTrue(result["checks"]["gitignore"]["ok"],
                        msg=result["checks"]["gitignore"]["problems"])
        self.assertIn("frontend/node_modules", result["checks"]["gitignore"]["ignored_dependency_dirs"])

    def test_react_java_variant_passes_structure_and_capacity(self):
        target = self.tmp / "react-java"
        generate(target, "--frontend", "react", "--backend", "java")
        result = self.verify(target)
        self.assertTrue(result["checks"]["structure"]["ok"], msg=result["checks"]["structure"]["problems"])
        self.assertEqual(result["checks"]["structure"]["frontend_stack"], "react")
        self.assertEqual(result["checks"]["structure"]["backend_stack"], "java")
        self.assertGreaterEqual(result["checks"]["capacity"]["capacity"], 30)
        self.assertTrue(result["ok"], msg=result["failures"])

    # ---------------- 基础环境自洽（G7） ----------------

    def test_python_syntax_error_is_rejected(self):
        """语法错误必须在交付前拦下，而不是等起服务才发现。"""
        copy = self.tmp / "syntax"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        target = copy / "backend" / "app" / "services" / "visitor.py"
        target.write_text(target.read_text(encoding="utf-8") + "\nBROKEN = true\n", encoding="utf-8")
        result = self.verify(copy)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["env_preconditions"]["ok"])
        joined = "；".join(result["checks"]["env_preconditions"]["problems"])
        self.assertTrue("true/false/null" in joined or "编译失败" in joined, msg=joined)

    def test_undeclared_python_dependency_is_rejected(self):
        copy = self.tmp / "dep-missing"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        target = copy / "backend" / "app" / "store.py"
        target.write_text("import httpx\n" + target.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.verify(copy)
        self.assertFalse(result["checks"]["env_preconditions"]["ok"])
        self.assertIn("httpx", result["checks"]["env_preconditions"]["evidence"]["python_undeclared"])

    def test_hardcoded_local_path_is_rejected(self):
        copy = self.tmp / "hardpath"
        shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
        target = copy / "frontend" / "src" / "api" / "client.ts"
        target.write_text("const HOST = '/Users/someone/projects/app'\n"
                          + target.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.verify(copy)
        self.assertFalse(result["checks"]["env_preconditions"]["ok"])
        self.assertTrue(any("本机绝对路径" in problem
                            for problem in result["checks"]["env_preconditions"]["problems"]))

    # ---------------- 推送到远端（默认 main） ----------------

    def test_publish_dry_run_does_not_touch_remote(self):
        repo = self.tmp / "pub-dry" / "app"
        generate(repo, "--git-init")
        bare = self.tmp / "pub-dry" / "remote.git"
        git("init", "-q", "--bare", str(bare))
        report = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare)))
        self.assertTrue(report["ok"])
        self.assertEqual(report["planned_action"], "create-branch")
        self.assertNotEqual(git("--git-dir", str(bare), "rev-parse", "--verify", "main").returncode, 0,
                         "dry-run 不应该往远端推任何东西")

    def test_publish_pushes_main_then_is_idempotent(self):
        repo = self.tmp / "pub" / "app"
        generate(repo, "--git-init")
        bare = self.tmp / "pub" / "remote.git"
        git("init", "-q", "--bare", str(bare))

        first = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                               execute=True, confirm="PUBLISH"))
        self.assertTrue(first["ok"], msg=first.get("problems"))
        self.assertTrue(first["pushed"])
        remote_sha = git("--git-dir", str(bare), "rev-parse", "main").stdout.strip()
        local_sha = git("-C", str(repo), "rev-parse", "main").stdout.strip()
        self.assertEqual(remote_sha, local_sha)
        self.assertEqual(first["remote_head_after"], local_sha)

        again = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                               execute=True, confirm="PUBLISH"))
        self.assertTrue(again["ok"])
        self.assertEqual(again["planned_action"], "up-to-date")
        self.assertFalse(again["pushed"])

    def test_publish_fast_forwards_new_commits(self):
        repo = self.tmp / "pub-ff" / "app"
        generate(repo, "--git-init")
        bare = self.tmp / "pub-ff" / "remote.git"
        git("init", "-q", "--bare", str(bare))
        publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                       execute=True, confirm="PUBLISH"))

        (repo / "README.md").write_text(
            (repo / "README.md").read_text(encoding="utf-8") + "\n说明补充\n", encoding="utf-8")
        git("-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a",
            "commit", "-qam", "docs: 补充说明")
        report = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                                execute=True, confirm="PUBLISH"))
        self.assertTrue(report["ok"], msg=report.get("problems"))
        self.assertEqual(report["planned_action"], "fast-forward")
        self.assertEqual(git("--git-dir", str(bare), "rev-parse", "main").stdout.strip(),
                         git("-C", str(repo), "rev-parse", "main").stdout.strip())

    def test_publish_refuses_diverged_remote(self):
        repo = self.tmp / "pub-diverge" / "app"
        generate(repo, "--git-init")
        bare = self.tmp / "pub-diverge" / "remote.git"
        git("init", "-q", "--bare", str(bare))
        publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                       execute=True, confirm="PUBLISH"))

        clone = self.tmp / "pub-diverge" / "clone"
        git("clone", "-q", str(bare), str(clone))
        git("-C", str(clone), "-c", "user.email=o@o", "-c", "user.name=o",
            "commit", "-q", "--allow-empty", "-m", "foreign")
        git("-C", str(clone), "push", "-q", "origin", "main")

        report = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                                execute=True, confirm="PUBLISH"))
        self.assertFalse(report["ok"])
        self.assertEqual(report["planned_action"], "diverged")
        self.assertTrue(any("不做强推" in problem for problem in report["problems"]))

    def test_publish_refuses_dirty_worktree(self):
        repo = self.tmp / "pub-dirty" / "app"
        generate(repo, "--git-init")
        bare = self.tmp / "pub-dirty" / "remote.git"
        git("init", "-q", "--bare", str(bare))
        (repo / "README.md").write_text("# 改动未提交\n", encoding="utf-8")
        report = publisher.publish(publish_args(repo=str(repo), remote_url=str(bare),
                                                execute=True, confirm="PUBLISH"))
        self.assertFalse(report["ok"])
        self.assertTrue(any("未提交改动" in problem for problem in report["problems"]))
        self.assertNotEqual(git("--git-dir", str(bare), "rev-parse", "--verify", "main").returncode, 0)

    def test_publish_requires_a_git_repo(self):
        target = self.tmp / "not-a-repo"
        target.mkdir()
        with self.assertRaises(SystemExit):
            publisher.publish(publish_args(repo=str(target)))


class VisibilityTest(unittest.TestCase):
    """生成的仓库推上去必须是公开的：下游容器要能匿名 clone。

    不联网：把 gh 的查询与改动两处调用替换掉，只验逻辑。
    """

    def setUp(self) -> None:
        self.edits: list[tuple[str, str]] = []
        self.original_read = publisher.gh_repo_visibility
        self.original_edit = publisher.edit_repo_visibility

    def tearDown(self) -> None:
        publisher.gh_repo_visibility = self.original_read
        publisher.edit_repo_visibility = self.original_edit

    def fake_repo(self, states: list[str | None]) -> None:
        """按顺序返回可见性：第一次读、改动后再读。"""
        queue = list(states)

        def read(full_name: str) -> str | None:
            return queue.pop(0) if queue else None

        def edit(full_name: str, visibility: str):
            self.edits.append((full_name, visibility))
            return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="", stderr="")

        publisher.gh_repo_visibility = read
        publisher.edit_repo_visibility = edit

    def test_already_public_needs_no_change(self):
        self.fake_repo(["public"])
        result = publisher.ensure_visibility("me/repo", "public", execute=True)
        self.assertEqual(result["before"], "public")
        self.assertEqual(result["after"], "public")
        self.assertFalse(result["fixed"])
        self.assertEqual(self.edits, [])
        self.assertEqual(result["problems"], [])

    def test_private_repo_is_switched_to_public(self):
        self.fake_repo(["private", "public"])
        result = publisher.ensure_visibility("me/repo", "public", execute=True)
        self.assertTrue(result["fixed"])
        self.assertEqual(result["after"], "public")
        self.assertEqual(self.edits, [("me/repo", "public")])
        self.assertEqual(result["problems"], [])

    def test_dry_run_only_plans_the_change(self):
        self.fake_repo(["private"])
        result = publisher.ensure_visibility("me/repo", "public", execute=False)
        self.assertTrue(result["will_fix"])
        self.assertFalse(result["fixed"])
        self.assertEqual(self.edits, [], "dry-run 不许真的改远端")
        self.assertIn("me/repo", result["planned_command"])
        self.assertIn("--visibility", result["planned_command"])

    def test_failed_switch_is_reported(self):
        self.fake_repo(["private", "private"])
        result = publisher.ensure_visibility("me/repo", "public", execute=True)
        self.assertFalse(result["fixed"])
        self.assertTrue(result["problems"], "改完还是 private 必须报出来")
        self.assertIn("gh repo edit me/repo --visibility public", result["problems"][0])

    def test_unreadable_visibility_does_not_block(self):
        """读不到可见性（网络/权限）时不能把整次推送拦死，标成 None 放行。"""
        self.fake_repo([None])
        result = publisher.ensure_visibility("me/repo", "public", execute=True)
        self.assertIsNone(result["before"])
        self.assertEqual(result["problems"], [])
        self.assertEqual(self.edits, [])

    def test_publish_plans_the_visibility_fix_in_dry_run(self):
        """接进 publish 之后，dry-run 的计划里要出现 gh repo edit。"""
        repo = Path(tempfile.mkdtemp(prefix="cc-repo-create-vis-")) / "app"
        try:
            generate(repo, "--git-init")
            original_exists = publisher.repo_exists
            original_head = publisher.remote_head
            publisher.repo_exists = lambda full_name: True
            publisher.remote_head = lambda url, branch, repo, allow_missing=False: ""
            self.fake_repo(["private", "private"])
            try:
                report = publisher.publish(publish_args(
                    repo=str(repo), owner="me", name="app", visibility="public",
                    skip_verify=True, execute=False))
            finally:
                publisher.repo_exists = original_exists
                publisher.remote_head = original_head
            self.assertTrue(report["visibility_will_fix"])
            commands = [" ".join(item) for item in report["planned_commands"]]
            self.assertTrue(any("gh repo edit me/app --visibility public" in item
                                for item in commands), commands)
        finally:
            shutil.rmtree(repo.parent, ignore_errors=True)


class NumberingTest(unittest.TestCase):
    """项目编号：<前缀 2~4 字母><5 位数>，首位是 1；本地已有的往后排，GitHub 上占用的也往后排。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-repo-create-number-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pick(self, **kwargs) -> dict:
        defaults = {"prefix": "cc", "explicit_number": None, "owner": None,
                    "check_github": False}
        defaults.update(kwargs)
        return scaffold_mod.pick_project_id(self.tmp, **defaults)

    def test_empty_dir_starts_at_10001(self):
        self.assertEqual(self.pick()["project_id"], "cc10001")

    def test_numbers_continue_from_what_is_already_there(self):
        (self.tmp / "cc10001").mkdir()
        (self.tmp / "cc10004").mkdir()
        self.assertEqual(self.pick()["project_id"], "cc10005")

    def test_prefix_is_swapped_but_the_sequence_keeps_going(self):
        (self.tmp / "cc10007").mkdir()
        picked = self.pick(prefix="YEL")
        self.assertEqual(picked["project_id"], "YEL10008")

    def test_prefix_case_is_preserved(self):
        self.assertEqual(self.pick(prefix="YEL")["project_id"], "YEL10001")
        (self.tmp / "cc10001").mkdir()
        self.assertEqual(self.pick(prefix="cc")["project_id"], "cc10002")

    def test_bad_prefix_is_rejected(self):
        for bad in ("c", "TOOLONG", "c1", ""):
            with self.assertRaises(SystemExit):
                self.pick(prefix=bad)

    def test_existing_github_repo_pushes_the_number_forward(self):
        taken = {"cc10001", "cc10002"}
        original = scaffold_mod.github_repo_exists
        scaffold_mod.github_repo_exists = lambda owner, name: name in taken
        try:
            picked = self.pick(check_github=True, owner="someone")
        finally:
            scaffold_mod.github_repo_exists = original
        self.assertEqual(picked["project_id"], "cc10003")
        self.assertTrue(picked["github_checked"])
        self.assertEqual([item["reason"] for item in picked["skipped"]],
                         ["GitHub 上已存在", "GitHub 上已存在"])

    def test_github_error_falls_back_to_local_sequence(self):
        """gh 查不了时不能卡住：回退成本地序号，并把 github_checked 标成 False。"""
        original = scaffold_mod.github_repo_exists
        scaffold_mod.github_repo_exists = lambda owner, name: None
        try:
            picked = self.pick(check_github=True, owner="someone")
        finally:
            scaffold_mod.github_repo_exists = original
        self.assertEqual(picked["project_id"], "cc10001")
        self.assertFalse(picked["github_checked"])

    def test_explicit_number_must_be_free(self):
        (self.tmp / "cc10009").mkdir()
        with self.assertRaises(SystemExit):
            self.pick(explicit_number=10009)
        self.assertEqual(self.pick(explicit_number=10010)["project_id"], "cc10010")
        with self.assertRaises(SystemExit):
            self.pick(explicit_number=20001)

    def test_parent_mode_creates_the_numbered_directory(self):
        out = self.tmp / "gen"
        out.mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "scaffold_repo.py"),
             "--parent", str(out), "--no-github-check", "--git-init"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-500:])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["project"]["project_id"], "cc10001")
        self.assertTrue((out / "cc10001" / ".gitignore").is_file())

        second = subprocess.run(
            [sys.executable, str(SCRIPTS / "scaffold_repo.py"),
             "--parent", str(out), "--prefix", "YEL", "--no-github-check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(second.returncode, 0, msg=second.stderr[-500:])
        self.assertEqual(json.loads(second.stdout)["project"]["project_id"], "YEL10002")

    def test_out_directory_name_is_recognized_as_a_project_id(self):
        out = self.tmp / "YEL12345"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "scaffold_repo.py"), "--out", str(out)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-500:])
        payload = json.loads(result.stdout)
        self.assertEqual(payload["project"]["project_id"], "YEL12345")
        self.assertEqual(payload["project"]["number"], 12345)


if __name__ == "__main__":
    unittest.main(verbosity=2)
