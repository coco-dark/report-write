"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from report_writing.domain.models import ChapterStatus, HumanDecision
from report_writing.services.pipeline import confirm_chapter, export_confirmed, rerun_chapter, run_task, update_points
from report_writing.services.store import TaskStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="report-writing", description="报告书写多 Agent 流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="学习模板并逐章检索、写作、检查")
    run.add_argument("--template", type=Path, required=True)
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--task", type=Path, required=True)
    run.add_argument("--cache", type=Path, default=Path("cache"))
    run.add_argument("--biz-type", required=True, help="资料业务类型，例如 001、002")
    run.add_argument("--force-study", action="store_true")

    status = sub.add_parser("status", help="查看任务状态和每章产物")
    status.add_argument("--task", type=Path, required=True)

    points = sub.add_parser("points", help="修改本次任务的章节要点，不回写模板缓存")
    points.add_argument("--task", type=Path, required=True)
    points.add_argument("--chapter", required=True)
    points.add_argument("--point", action="append", required=True)
    points.add_argument("--save-cache", action="store_true")
    points.add_argument("--cache", type=Path, default=Path("cache"))

    rerun = sub.add_parser("rerun", help="沿用当前书写要求，重新检索、写作和检查")
    rerun.add_argument("--task", type=Path, required=True)
    rerun.add_argument("--template", type=Path, required=True)
    rerun.add_argument("--dataset", type=Path, required=True)

    confirm = sub.add_parser("confirm", help="人工确认一个章节")
    confirm.add_argument("--task", type=Path, required=True)
    confirm.add_argument("--template", type=Path, required=True)
    confirm.add_argument("--dataset", type=Path, required=True)
    confirm.add_argument("--chapter", required=True)
    confirm.add_argument("--decision", choices=[item.value for item in HumanDecision], required=True)
    confirm.add_argument("--text", default="")
    confirm.add_argument("--comment", default="")

    export = sub.add_parser("export", help="导出已确认章节正文")
    export.add_argument("--task", type=Path, required=True)

    serve = sub.add_parser("serve", help="启动 FastAPI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    serve.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="代码变更时自动重载（默认读环境变量 REPORT_WRITING_RELOAD，dev-serve 会开启）",
    )

    sync_data = sub.add_parser("sync-data", help="将各用户 workspace 同步到 MySQL（按 user_id）")
    sync_data.add_argument("--home", type=Path, default=None, help="默认 REPORT_WRITING_HOME 或 data")

    repair = sub.add_parser("repair-user-data", help="修复用户目录嵌套，并可选隔离顶层旧版全局 data")
    repair.add_argument("--home", type=Path, default=None, help="默认 REPORT_WRITING_HOME 或 data")
    repair.add_argument(
        "--quarantine-legacy",
        action="store_true",
        help="将 data/tasks、robots 等顶层旧目录移到 data/_legacy_global（勿删，仅隔离）",
    )
    repair.add_argument("--resync-db", action="store_true", help="修复后按用户重新 sync-data 到 MySQL")

    args = parser.parse_args(argv)
    if args.command == "run":
        task = run_task(args.template, args.dataset, args.task, args.cache, args.biz_type, args.force_study)
        print(_summary(task))
        return 0
    if args.command == "status":
        print(_summary(TaskStore(args.task).load()))
        return 0
    if args.command == "points":
        task = update_points(args.task, args.cache, args.chapter, args.point, args.save_cache)
        print(_summary(task))
        return 0
    if args.command == "rerun":
        task = rerun_chapter(args.task, args.template, args.dataset)
        print(_summary(task))
        return 0
    if args.command == "confirm":
        task = confirm_chapter(
            args.task,
            args.template,
            args.dataset,
            args.chapter,
            HumanDecision(args.decision),
            args.text,
            args.comment,
        )
        print(_summary(task))
        return 0
    if args.command == "export":
        print(json.dumps(export_confirmed(TaskStore(args.task).load()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "sync-data":
        import os

        from report_writing.services.data_sync import sync_all_users_local_data

        from report_writing.infra.project_config import data_home_path

        home = args.home or data_home_path()
        counts = sync_all_users_local_data(home)
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0
    if args.command == "repair-user-data":
        import os

        from report_writing.services.data_sync import sync_all_users_local_data
        from report_writing.services.users import repair_user_data_layout

        from report_writing.infra.project_config import data_home_path

        home = args.home or data_home_path()
        report = repair_user_data_layout(home, quarantine_legacy=bool(args.quarantine_legacy))
        if args.resync_db:
            report["resync"] = sync_all_users_local_data(home)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        _run_serve(args)
        return 0
    parser.error(f"未知命令: {args.command}")
    return 1


def _serve_reload_dirs() -> list[str]:
    pkg = Path(__file__).resolve().parent
    root = pkg.parents[3]
    dirs = [str(pkg.parent)]
    config_dir = root / "config"
    if config_dir.is_dir():
        dirs.append(str(config_dir))
    return dirs


def _run_serve(args: argparse.Namespace) -> None:
    import uvicorn

    reload = args.reload
    if reload is None:
        reload = os.environ.get("REPORT_WRITING_RELOAD", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    kwargs: dict = {"host": args.host, "port": args.port}
    if reload:
        kwargs["reload"] = True
        kwargs["reload_dirs"] = _serve_reload_dirs()
    uvicorn.run("report_writing.api:app", **kwargs)


def _summary(task) -> str:
    lines = [
        f"任务 {task.id}  状态 {task.status.value}  学习 {'缓存' if task.brief_from_cache else '新生成'}",
    ]
    for chapter in task.chapters:
        adopted = len(chapter.adopted_evidence())
        lines.append(f"- {chapter.key} {chapter.title}  {chapter.status.value}  采纳证据 {adopted}")
        if chapter.status == ChapterStatus.NEEDS_HUMAN and chapter.human_note:
            lines.append(f"  待人工：{chapter.human_note}")
    return "\n".join(lines)
