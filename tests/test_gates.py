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
import probe_startup as probe  # noqa: E402
import scaffold_repo as scaffold_mod  # noqa: E402
import verify_repo as verifier  # noqa: E402

SPEC = SKILL_DIR / "examples" / "spec-film-production.json"
# 历史遗留：这份 spec 是「园区通行与安全运营平台」，因为含 工单 / 库存 / 权限 / 预约
# 四个禁用模块，已经不能作为出厂默认。留在这里当严禁题域的反例。
BANNED_SPEC = SKILL_DIR / "tests" / "fixtures" / "spec-park-pass-banned.json"


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
        self.assertIn("script", first)

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
        target = copy / "backend" / "app" / "services" / "script.py"
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
        # 外层 <编号> 是任务容器，内层 <编号> 才是仓库根
        self.assertEqual(Path(payload["project"]["container"]), (out / "cc10001").resolve())
        self.assertEqual(Path(payload["out"]), (out / "cc10001" / "cc10001").resolve())
        self.assertEqual(Path(payload["repo_root"]), (out / "cc10001" / "cc10001").resolve())
        self.assertTrue((out / "cc10001" / "cc10001" / ".gitignore").is_file())
        self.assertTrue((out / "cc10001" / "cc10001" / ".git").is_dir())
        self.assertTrue((out / "cc10001" / "cc10001" / "frontend").is_dir())
        self.assertTrue((out / "cc10001" / "cc10001" / "backend").is_dir())
        # 仓库根不能直接落在外层容器上
        self.assertFalse((out / "cc10001" / ".gitignore").exists())

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


class ProbeEndpointDiscovery(unittest.TestCase):
    """探针不该把业务接口写死成某个题域的模块名，而要从仓库里自己找。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probe-discovery-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_python_routers_are_discovered_and_init_is_skipped(self):
        routers = self.tmp / "backend" / "app" / "routers"
        routers.mkdir(parents=True)
        (routers / "__init__.py").write_text('prefix = "/api/__init__"\n', encoding="utf-8")
        (routers / "waybill.py").write_text(
            'router = APIRouter(prefix="/api/waybill")\n', encoding="utf-8")
        (routers / "order.py").write_text(
            "router = APIRouter(prefix='/api/order')\n", encoding="utf-8")
        found = probe.discover_business_endpoints(self.tmp / "backend")
        self.assertEqual(found, ["/api/order", "/api/waybill"])

    def test_java_request_mapping_is_discovered(self):
        java = self.tmp / "backend" / "src" / "main" / "java" / "com" / "demo"
        java.mkdir(parents=True)
        (java / "ColdOrderController.java").write_text(
            '@RequestMapping("/api/order")\n', encoding="utf-8")
        found = probe.discover_business_endpoints(self.tmp / "backend")
        self.assertEqual(found, ["/api/order"])

    def test_falls_back_to_default_spec_key_when_nothing_is_found(self):
        # 兜底接口跟着默认 spec 的第一个模块走，不写死题域。
        expected = f"/api/{probe.default_spec_module_key()}"
        self.assertEqual(probe.discover_business_endpoints(None), [expected])
        empty = self.tmp / "backend"
        empty.mkdir()
        self.assertEqual(probe.discover_business_endpoints(empty), [expected])

    def test_generated_default_spec_repo_is_probed_at_its_first_module(self):
        target = self.tmp / "default-spec-repo"
        generate(target, "--spec", str(SPEC))
        found = probe.discover_business_endpoints(target / "backend")
        self.assertIn(f"/api/{probe.default_spec_module_key()}", found)
        self.assertEqual(found, sorted(found))


class BannedTopics(unittest.TestCase):
    """严禁出题题域：spec 预检与仓库验收两处都要拦得住，且没有绕过口子。

    这条闸门来自 0731 方案规则 §6「严禁出题清单（命中即废题）」。跟禁用题型不同，
    它判的是**题域**本身：仓库做得再合规，命中就是废题。
    """

    # 一个干净的模块模板：任何字段都不含禁用词。
    CLEAN_MODULE = {
        "key": "inspect",
        "name": "设备点检",
        "entity": "点检记录",
        "fields": ["点检单号", "设备名称", "点检部位", "点检结果", "点检人", "点检时间"],
        "statuses": ["待点检", "点检中", "已合格", "已异常"],
        "actions": ["开始点检", "判定合格", "登记异常"],
        "action_targets": ["点检中", "已合格", "已异常"],
        "metrics": ["今日点检", "待点检设备", "异常记录"],
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="banned-topic-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_spec(self, name: str, module: dict, **top) -> Path:
        spec = {
            "app_name": "设备点检与润滑管理平台",
            "app_slug": "equipment-inspection",
            "app_desc": "面向设备点检、润滑加注、油脂化验与检修验收的一体化后台。",
            "modules": [module],
        }
        spec.update(top)
        path = self.tmp / name
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return path

    def check_spec(self, spec: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "scaffold_repo.py"),
             "--check-spec", "--spec", str(spec)],
            capture_output=True, text=True, check=False)

    def verify(self, repo: Path) -> dict:
        return verifier.verify(repo, min_lines=2000, min_capacity=30,
                               per_mode=rules.MAX_PER_MODE, per_subject=rules.MAX_PER_SUBJECT)

    # ---------------- 规则快照本身 ----------------

    def test_default_example_spec_is_clean(self):
        """出厂默认 spec 必须永远干净，否则 skill 开箱即不可用。"""
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        self.assertEqual(scaffold_mod.scan_spec_topics(spec), [])

    def test_snapshot_declares_its_source(self):
        self.assertIn("0731", rules.BANNED_TOPIC_SOURCE)
        self.assertEqual(set(rules.BANNED_TOPIC_KEYWORDS), set(rules.BANNED_TOPIC_CATEGORY_LABELS))

    def test_scan_matches_substring_and_is_case_insensitive(self):
        self.assertEqual(rules.scan_banned_topics("Shopping-Cart 模块")[0][1], "shopping-cart")
        self.assertEqual(rules.scan_banned_topics("设备点检"), [])

    # ---------------- spec 预检 ----------------

    def test_historical_park_spec_is_now_refused(self):
        """出过题的旧默认 spec：含工单/库存/权限/预约，现在必须生成不了。"""
        result = self.check_spec(BANNED_SPEC)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("严禁出题清单", result.stdout)
        for word in ("工单", "库存", "权限", "预约"):
            self.assertIn(word, result.stdout)

    def test_banned_module_name_is_refused(self):
        spec = self.write_spec("banned-name.json",
                               dict(self.CLEAN_MODULE, key="spare", name="备件库存"))
        result = self.check_spec(spec)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("库存", result.stdout)

    def test_banned_keyword_only_in_fields_is_refused(self):
        """命中点只在业务字段里也要拦——扫的是 spec 全字段。"""
        module = dict(self.CLEAN_MODULE, fields=["点检单号", "设备名称", "预约时段", "点检结果"])
        result = self.check_spec(self.write_spec("banned-field.json", module))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("预约", result.stdout)

    def test_banned_keyword_only_in_app_desc_is_refused(self):
        spec = self.write_spec("banned-desc.json", dict(self.CLEAN_MODULE),
                               app_desc="面向电商卖家的点检后台。")
        result = self.check_spec(spec)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("电商", result.stdout)

    def test_clean_spec_passes_check_spec(self):
        result = self.check_spec(self.write_spec("clean.json", dict(self.CLEAN_MODULE)))
        self.assertEqual(result.returncode, 0, msg=result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_generate_refuses_banned_spec_and_writes_nothing(self):
        spec = self.write_spec("banned-gen.json",
                               dict(self.CLEAN_MODULE, entity="维保工单"))
        out = self.tmp / "never-created"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "scaffold_repo.py"),
             "--out", str(out), "--spec", str(spec)],
            capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("工单", result.stderr)
        self.assertFalse(out.exists(), "被拒的 spec 不该落下任何目录")

    # ---------------- 仓库验收兜底 ----------------

    def test_readme_with_banned_word_is_rejected(self):
        repo = self.tmp / "readme-hit"
        generate(repo)
        with (repo / "README.md").open("a", encoding="utf-8") as handle:
            handle.write("\n## 客服工单\n")
        compliance = self.verify(repo)["checks"]["compliance"]
        self.assertFalse(compliance["ok"])
        self.assertIn("工单", "；".join(compliance["banned_topic_hits"]))

    def test_banned_word_only_in_source_is_rejected(self):
        """比下游只读 README 更严一档：源码里出现也算命中。"""
        repo = self.tmp / "source-hit"
        generate(repo)
        page = repo / "frontend" / "src" / "views" / "script" / "index.vue"
        page.write_text(page.read_text(encoding="utf-8") + "\n<!-- 备件库存盘点 -->\n",
                        encoding="utf-8")
        compliance = self.verify(repo)["checks"]["compliance"]
        self.assertFalse(compliance["ok"])
        self.assertIn("库存", "；".join(compliance["banned_topic_hits"]))

    def test_repo_directory_name_with_banned_word_is_rejected(self):
        """下游是拿仓库名判的，目录名里带禁用词同样拦。"""
        repo = self.tmp / "app-inventory"
        generate(repo)
        compliance = self.verify(repo)["checks"]["compliance"]
        self.assertFalse(compliance["ok"])
        self.assertIn("inventory", "；".join(compliance["banned_topic_hits"]))

    def test_clean_generated_repo_reports_no_banned_topic(self):
        repo = self.tmp / "clean-repo"
        generate(repo)
        result = self.verify(repo)
        self.assertTrue(result["ok"], msg=result["failures"])
        self.assertEqual(result["checks"]["compliance"]["banned_topic_hits"], [])

    # ---------------- 清单随上游漂移 ----------------

    def test_banned_drift_matches_identical_table(self):
        fake = self.tmp / "same_gate.py"
        fake.write_text("BANNED_KEYWORDS = %r\n" % (rules.BANNED_TOPIC_KEYWORDS,),
                        encoding="utf-8")
        result = verifier.banned_drift(fake)
        self.assertTrue(result["ok"], msg=result["diffs"])
        self.assertEqual(result["their_banned_keywords"], "BANNED_KEYWORDS")

    def test_banned_drift_detects_table_difference(self):
        fake = self.tmp / "drifted_gate.py"
        fake.write_text("BANNED_KEYWORDS = {'business_system': ['电商']}\n", encoding="utf-8")
        result = verifier.banned_drift(fake)
        self.assertFalse(result["ok"])
        self.assertTrue(any("分类" in diff for diff in result["diffs"]), msg=result["diffs"])

    def test_banned_drift_reports_absent_table(self):
        fake = self.tmp / "no_table.py"
        fake.write_text("REPO_SOURCE_PATTERNS = ()\n", encoding="utf-8")
        result = verifier.banned_drift(fake)
        self.assertFalse(result["ok"])
        self.assertEqual(result["their_banned_keywords"], "absent")

    def test_rules_drift_does_not_fail_when_banned_table_absent(self):
        """老的 check_repo_theme.py 不带这份清单，不该因此把整次比对判成失败。"""
        fake = self.tmp / "theme_gate.py"
        fake.write_text(
            "REPO_SOURCE_PATTERNS = %r\nPARENT_NAMED_STEMS = %r\nGENERIC_DIR_NAMES = %r\n"
            "GENERIC_TOKENS = %r\nIGNORED_SOURCE_DIR_PARTS = %r\nDEMAND_MODE_LEXICON = %r\n"
            % (rules.REPO_SOURCE_PATTERNS, rules.PARENT_NAMED_STEMS, rules.GENERIC_DIR_NAMES,
               rules.GENERIC_TOKENS, rules.IGNORED_SOURCE_DIR_PARTS, rules.DEMAND_MODE_LEXICON),
            encoding="utf-8")
        result = verifier.rules_drift(fake)
        self.assertTrue(result["ok"], msg=result["diffs"])
        self.assertEqual(result["their_banned_keywords"], "absent")


class PipelineTest(unittest.TestCase):
    """一条命令跑到底的驱动：生成 -> 静态验收 -> 真启动 -> 推 GitHub。

    这里只覆盖不联网的部分（生成 / 静态验收 / 停止策略 / 推送闸门）；
    真启动与真推送由 probe_startup.py / publish_github.py 自己的用例负责。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-repo-create-pipeline-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pipeline(self, *extra: str):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "run_pipeline.py"), "--parent", str(self.tmp / "gen"),
             "--no-github-check", "--skip-probe", *extra],
            capture_output=True, text=True, check=False)

    def test_pipeline_runs_generate_and_verify_into_nested_layout(self):
        result = self.pipeline("--spec", str(SPEC))
        self.assertEqual(result.returncode, 0, msg=result.stdout[-800:] + result.stderr[-400:])
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertTrue(report["ok"])
        self.assertFalse(report["published"], "没加 --publish 就不该推")
        self.assertEqual(report["specs_done"], 1)

        repo = report["repos"][0]
        self.assertEqual(repo["project_id"], "cc10001")
        self.assertEqual(repo["steps"]["推送 GitHub"], "skipped")
        self.assertTrue(repo["steps"]["静态验收"])
        self.assertGreaterEqual(repo["capacity"], 30)
        # 仓库根必须落在第二层 <编号>/<编号>
        self.assertTrue(repo["repo_root"].endswith("cc10001/cc10001"))
        self.assertTrue((self.tmp / "gen" / "cc10001" / "cc10001" / ".gitignore").is_file())
        self.assertTrue((self.tmp / "gen" / "cc10001" / "cc10001" / ".git").is_dir())

    def test_pipeline_refuses_publish_without_confirmation(self):
        result = self.pipeline("--spec", str(SPEC), "--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm PUBLISH", result.stderr)
        self.assertFalse((self.tmp / "gen").exists(), "参数被拒时不该落下任何仓库")

    def test_pipeline_stops_early_when_a_spec_is_refused(self):
        """第一份命中严禁题域就停，默认不硬撑着跑后面那份。"""
        result = self.pipeline("--spec", str(BANNED_SPEC), "--spec", str(SPEC))
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertFalse(report["ok"])
        self.assertTrue(report["stopped_early"])
        self.assertEqual(report["specs_done"], 1)
        self.assertEqual(report["repos"][0]["failed_step"], "生成")
        # 失败原因要带上命中点（reason 会截断，认关键字即可）
        self.assertIn("库存", report["repos"][0]["reason"])
        # 被拒的 spec 不落目录，第二份也没被生成
        self.assertFalse((self.tmp / "gen" / "cc10002").exists())

    def test_pipeline_keep_going_runs_the_rest(self):
        result = self.pipeline("--spec", str(BANNED_SPEC), "--spec", str(SPEC), "--keep-going")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(report["specs_done"], 2)
        self.assertFalse(report["stopped_early"])
        self.assertFalse(report["repos"][0]["ok"])
        self.assertTrue(report["repos"][1]["ok"])

    def test_pipeline_publishes_to_a_remote_and_confirms_sha(self):
        """推送这一步真的接上了：推到本地裸仓库，远端 main 的 SHA 必须等于本地 HEAD。"""
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        result = self.pipeline("--spec", str(SPEC), "--publish", "--confirm", "PUBLISH",
                               "--remote-url", str(bare))
        self.assertEqual(result.returncode, 0, msg=result.stdout[-1200:])
        report = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertTrue(report["published"])
        repo = report["repos"][0]
        self.assertTrue(repo["ok"])
        self.assertTrue(repo["steps"]["推送 GitHub"])
        self.assertTrue(repo["remote_matches_local"])

        remote_sha = subprocess.run(
            ["git", "--git-dir", str(bare), "rev-parse", "main"],
            capture_output=True, text=True, check=True).stdout.strip()
        local_sha = subprocess.run(
            ["git", "-C", repo["repo_root"], "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(remote_sha, local_sha)


if __name__ == "__main__":
    unittest.main(verbosity=2)
