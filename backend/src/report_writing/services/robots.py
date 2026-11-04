"""书写机器人：资源绑定与提示词/知识库解析。"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from report_writing.domain.models import (
    ChatRequest,
    ChatResponse,
    GenerateRobotPromptsRequest,
    GenerateRobotPromptsResponse,
    RenderTemplateSummary,
    Robot,
    RobotSummary,
)

_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_schema_ready = False

DEFAULT_CHAT_PROMPT = (
    "你是智能文档协作助手，一位靠谱、好说话的协作搭档，帮用户写报告、函件、方案、说明等各类文档。"
    "像真人同事聊天：语气自然、口语化一点，先接住对方意图，再给清晰下一步；"
    "少用「请」「已」「当前」这类公文腔，也不要一上来甩术语或流程说明。"
    "未选机器人时用通用能力帮忙；已选机器人时，仍保持这种说话方式，并遵循该机器人的职责与边界。"
    "不要编造用户没给的真实项目数据或隐私；缺信息时用【占位】标出，并随口问一句。"
    "结构可以清楚（短段落、列表），但读起来要像人在说话，不要像系统通知。"
)

# 叠加到任意机器人 chat_prompt 上的语气约束（不覆盖其角色，只约束说话方式）
CHAT_TONE_HINT = (
    "\n\n【说话方式】像身边懂行的同事：亲切、简短、有来有回；"
    "先回应再指路；可用「我帮你」「咱们」「一会儿」；"
    "避免机械复读状态/任务号；需要操作时用一两句说清，并点出下方可点的按钮。"
)

PromptKind = Literal["chat", "study", "write"]


def robots_root(home: Path) -> Path:
    root = Path(home) / "robots"
    root.mkdir(parents=True, exist_ok=True)
    return root


_ROBOT_PARENT = "writing_robot"
_ROBOT_CHILD = "writing_robot_document"
_ROBOT_META_DDL = """
CREATE TABLE IF NOT EXISTS writing_robot (
    id varchar(64) NOT NULL PRIMARY KEY,
    name varchar(255) NOT NULL DEFAULT '',
    description text,
    created_at varchar(32) NOT NULL,
    updated_at varchar(32) NOT NULL,
    KEY writing_robot_updated_idx (updated_at)
)
"""
_ROBOT_CHILD_DDL = """
CREATE TABLE IF NOT EXISTS writing_robot_document (
    robot_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL
)
"""


def list_robots(home: Path) -> list[RobotSummary]:
    from report_writing.services.users import current_user_id, owns_user_resource

    uid = current_user_id()
    if _use_mysql():
        return _db_list_robots(uid)
    items: list[RobotSummary] = []
    for path in sorted(robots_root(home).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            robot = _load_path(path)
        except Exception:
            continue
        if not owns_user_resource(uid, getattr(robot, "user_id", "") or ""):
            continue
        items.append(_summary(robot))
    return items


def get_robot(home: Path, robot_id: str) -> Robot | None:
    from report_writing.services.users import current_user_id, owns_user_resource

    rid = _require_id(robot_id)
    uid = current_user_id()
    if _use_mysql():
        found = _db_get_robot(rid)
        if found is not None:
            if not owns_user_resource(uid, getattr(found, "user_id", "") or ""):
                return None
            return found
        local = _file_get_robot(home, rid)
        if local is not None:
            if not owns_user_resource(uid, getattr(local, "user_id", "") or ""):
                return None
            _db_save_robot(local)
            return local
        return None
    found = _file_get_robot(home, rid)
    if found is None:
        return None
    if not owns_user_resource(uid, getattr(found, "user_id", "") or ""):
        return None
    return found


def create_robot(home: Path, payload: dict | Robot) -> Robot:
    data = payload.to_dict() if isinstance(payload, Robot) else dict(payload or {})
    robot_id = str(data.get("id") or "").strip() or f"r{uuid.uuid4().hex[:10]}"
    robot_id = _require_id(robot_id)
    if get_robot(home, robot_id) is not None:
        raise ValueError(f"机器人已存在：{robot_id}")
    from report_writing.services.users import current_user_id

    now = _now()
    robot = Robot(
        id=robot_id,
        user_id=current_user_id(),
        name=str(data.get("name") or "").strip() or "未命名机器人",
        description=str(data.get("description") or "").strip(),
        chat_prompt=str(data.get("chat_prompt") or "").strip(),
        study_prompt=str(data.get("study_prompt") or "").strip(),
        write_prompt=str(data.get("write_prompt") or "").strip(),
        blueprint_ids=_str_list(data.get("blueprint_ids")),
        render_template_ids=_str_list(data.get("render_template_ids")),
        word_style_ids=_str_list(data.get("word_style_ids")),
        knowledge_folder_ids=_str_list(data.get("knowledge_folder_ids")),
        element_library_ids=_str_list(data.get("element_library_ids")),
        materials_pack_ids=_str_list(data.get("materials_pack_ids")),
        created_at=now,
        updated_at=now,
    )
    _persist_robot(home, robot)
    return robot


def update_robot(home: Path, robot_id: str, payload: dict | Robot) -> Robot:
    from report_writing.services.users import current_user_id

    existing = get_robot(home, robot_id)
    if existing is None:
        raise KeyError(robot_id)
    data = payload.to_dict() if isinstance(payload, Robot) else dict(payload or {})
    robot = Robot(
        id=existing.id,
        user_id=getattr(existing, "user_id", "") or current_user_id(),
        name=str(data.get("name") if "name" in data else existing.name).strip() or existing.name,
        description=str(data.get("description") if "description" in data else existing.description).strip(),
        chat_prompt=str(data.get("chat_prompt") if "chat_prompt" in data else existing.chat_prompt).strip(),
        study_prompt=str(data.get("study_prompt") if "study_prompt" in data else existing.study_prompt).strip(),
        write_prompt=str(data.get("write_prompt") if "write_prompt" in data else existing.write_prompt).strip(),
        blueprint_ids=_str_list(data["blueprint_ids"]) if "blueprint_ids" in data else list(existing.blueprint_ids),
        render_template_ids=_str_list(data["render_template_ids"])
        if "render_template_ids" in data
        else list(existing.render_template_ids),
        word_style_ids=_str_list(data["word_style_ids"])
        if "word_style_ids" in data
        else list(getattr(existing, "word_style_ids", None) or []),
        knowledge_folder_ids=_str_list(data["knowledge_folder_ids"])
        if "knowledge_folder_ids" in data
        else list(existing.knowledge_folder_ids),
        element_library_ids=_str_list(data["element_library_ids"])
        if "element_library_ids" in data
        else list(getattr(existing, "element_library_ids", None) or []),
        materials_pack_ids=_str_list(data["materials_pack_ids"])
        if "materials_pack_ids" in data
        else list(getattr(existing, "materials_pack_ids", None) or []),
        created_at=existing.created_at,
        updated_at=_now(),
    )
    _persist_robot(home, robot)
    return robot


def delete_robot(home: Path, robot_id: str) -> None:
    rid = _require_id(robot_id)
    if _use_mysql():
        if not _db_delete_robot(rid):
            raise KeyError(robot_id)
        path = robots_root(home) / f"{rid}.json"
        if path.is_file():
            path.unlink(missing_ok=True)
        return
    path = robots_root(home) / f"{rid}.json"
    if not path.is_file():
        raise KeyError(robot_id)
    path.unlink()


def resolve_knowledge_folders(
    node_folder_ids: list[str] | None,
    robot: Robot | None = None,
    *,
    project_folder_ids: list[str] | None = None,
    knowledge_scope: str = "both",
) -> list[str] | None:
    """合并要素知识库范围与写作任务项目资料根。

    - shared：仅要素上的通用文件夹
    - project：仅任务 project_folder_ids
    - both（默认）：要素文件夹 ∪ 项目根
    机器人目录不再参与检索。返回 None 表示未限定（全库）。
    """
    scope = (knowledge_scope or "both").strip().lower() or "both"
    shared = _str_list(node_folder_ids)
    project = _str_list(project_folder_ids)
    if scope == "shared":
        return shared or None
    if scope == "project":
        return project or None
    # both
    merged: list[str] = []
    seen: set[str] = set()
    for item in shared + project:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged or None


def resolve_prompt(
    kind: PromptKind,
    robot: Robot | None,
    *,
    default: str,
) -> str:
    if robot is None:
        base = default
    else:
        field = {
            "chat": robot.chat_prompt,
            "study": robot.study_prompt,
            "write": robot.write_prompt,
        }.get(kind) or ""
        base = str(field).strip() or default
    if kind == "chat":
        return f"{base.rstrip()}{CHAT_TONE_HINT}"
    return base


def filter_blueprints_for_robot(items: list, robot: Robot | None) -> list:
    """未选机器人：保留 items（须已是当前用户蓝本）；已选机器人：仅 blueprint_ids，未绑定则 []。"""
    if robot is None:
        return list(items or [])
    allowed = _str_list(robot.blueprint_ids)
    if not allowed:
        return []
    allow = set(allowed)
    return [item for item in (items or []) if getattr(item, "id", None) in allow]


def filter_element_library_for_robot(items: list, robot: Robot | None) -> list:
    """未选机器人：全部个人要素；已选：仅 element_library_ids，未绑定则 []。"""
    if robot is None:
        return list(items or [])
    allowed = _str_list(getattr(robot, "element_library_ids", None) or [])
    if not allowed:
        return []
    allow = set(allowed)
    return [item for item in (items or []) if str(getattr(item, "id", "") or "") in allow]


def filter_materials_packs_for_robot(items: list, robot: Robot | None) -> list:
    """未选机器人：全部资料包；已选：仅 materials_pack_ids，未绑定则 []。"""
    if robot is None:
        return list(items or [])
    allowed = _str_list(getattr(robot, "materials_pack_ids", None) or [])
    if not allowed:
        return []
    allow = set(allowed)
    return [item for item in (items or []) if str(getattr(item, "id", "") or "") in allow]


def robot_for_session(home: Path, session) -> Robot | None:
    rid = str(getattr(session, "robot_id", "") or "").strip()
    if not rid:
        return None
    return get_robot(home, rid)


def scoped_element_library_items(home: Path, robot_id: str | None = None):
    from report_writing.services.element_library import list_library_items

    all_user = list_library_items(Path(home))
    rid = (robot_id or "").strip()
    if not rid:
        return all_user
    robot = get_robot(home, rid)
    if robot is None:
        return []
    return filter_element_library_for_robot(all_user, robot)


def scoped_materials_packs(home: Path, robot_id: str | None = None, **list_kwargs):
    from report_writing.services.materials_pack import list_packs

    all_user = list_packs(Path(home), **list_kwargs)
    rid = (robot_id or "").strip()
    if not rid:
        return all_user
    robot = get_robot(home, rid)
    if robot is None:
        return []
    return filter_materials_packs_for_robot(all_user, robot)


def assert_materials_pack_in_robot_scope(home: Path, robot_id: str, pack_id: str) -> None:
    rid = (robot_id or "").strip()
    pid = (pack_id or "").strip()
    if not rid or not pid:
        return
    robot = get_robot(home, rid)
    if robot is None:
        raise ValueError("当前机器人不存在")
    allowed = _str_list(getattr(robot, "materials_pack_ids", None) or [])
    if allowed and pid not in allowed:
        raise ValueError("该资料包不在当前机器人绑定范围内")


def scoped_render_templates(home: Path, robot_id: str | None = None) -> list[RenderTemplateSummary]:
    """未选机器人：当前用户全部渲染模版；已选：仅该机器人绑定的模版。"""
    from report_writing.services.render import list_templates

    all_user = list_templates(Path(home))
    rid = (robot_id or "").strip()
    if not rid:
        return all_user
    robot = get_robot(home, rid)
    if robot is None:
        return []
    bound = _str_list(robot.render_template_ids)
    if not bound:
        return []
    allowed = set(bound)
    return [item for item in all_user if str(item.id) in allowed]


def scoped_word_styles(home: Path, robot_id: str | None = None):
    """未选机器人：当前用户全部 Word 样式；已选：仅该机器人绑定的样式。"""
    from report_writing.services.word_styles import list_word_styles

    all_user = list_word_styles(Path(home))
    rid = (robot_id or "").strip()
    if not rid:
        return all_user
    robot = get_robot(home, rid)
    if robot is None:
        return []
    bound = _str_list(getattr(robot, "word_style_ids", None) or [])
    if not bound:
        return []
    allowed = set(bound)
    return [item for item in all_user if str(item.id) in allowed]


_QUERY_STOP = (
    "帮我",
    "我想",
    "我要",
    "一下",
    "一个",
    "一份",
    "这个",
    "那个",
    "可以",
    "怎么",
    "如何",
    "请你",
    "请",
    "想要",
    "开始",
    "进行",
    "使用",
    "看看",
    "有哪些",
    "什么",
    "在帮",
    "再帮",
)

# 仅命中这些泛词时不算「对口」——避免「述职报告」靠「报告」命中「安全报告」
_GENERIC_MATCH_TERMS = frozenset(
    {
        "报告",
        "文档",
        "材料",
        "文件",
        "写作",
        "蓝本",
        "样板",
        "大纲",
        "目录",
        "章节",
        "内容",
        "项目",
        "工作",
        "说明",
    }
)


def _blueprint_haystack(item) -> str:
    return " ".join(
        [
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "category", "") or ""),
            str(getattr(item, "description", "") or ""),
            " ".join(getattr(item, "tags", None) or []),
        ]
    ).lower()


def _query_terms(q: str) -> list[str]:
    """从中文问句抽出可匹配词（含用途说明里的片段，避免整句当 token）。"""
    text = (q or "").strip().lower()
    if not text:
        return []
    cleaned = text
    for stop in _QUERY_STOP:
        cleaned = cleaned.replace(stop, " ")
    terms: list[str] = []
    for part in re.split(r"[\s,，、。！？；;:：\|｜/]+", cleaned):
        part = part.strip()
        if len(part) >= 2:
            terms.append(part)
    # 连续中文里再切 2～6 字片段，提高「安全预评价」命中「用于写安全预评价报告」
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned.replace(" ", "")):
        if chunk not in terms:
            terms.append(chunk)
        for width in (4, 3, 2):
            if len(chunk) <= width:
                continue
            for i in range(0, len(chunk) - width + 1):
                piece = chunk[i : i + width]
                if piece not in terms:
                    terms.append(piece)
    # 去重保序
    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        if term in seen or term in _QUERY_STOP:
            continue
        seen.add(term)
        ordered.append(term)
    return ordered[:40]


def search_blueprints(
    home: Path,
    *,
    q: str = "",
    robot_id: str = "",
    enabled_only: bool = True,
) -> list:
    """蓝本范围：未选 robot_id → 当前用户全部启用蓝本；已选 → 仅该机器人 blueprint_ids。"""
    from report_writing.controller import list_blueprints

    items = list_blueprints(Path(home) / "tasks", include_disabled=not enabled_only)
    if enabled_only:
        items = [item for item in items if bool(getattr(item, "enabled", True))]
    robot = get_robot(home, robot_id) if (robot_id or "").strip() else None
    items = filter_blueprints_for_robot(items, robot)
    query = (q or "").strip().lower()
    if not query:
        return items
    terms = _query_terms(query)
    if not terms:
        return items

    distinctive = [t for t in terms if t not in _GENERIC_MATCH_TERMS and len(t) >= 2]

    def score(item) -> tuple[int, int]:
        """返回 (总分, 特色词命中数)；仅靠泛词命中视为 0。"""
        hay = _blueprint_haystack(item)
        if not hay.strip():
            return 0, 0
        points = 0
        hit_distinct = 0
        # 用途说明权重大于标题
        desc = str(getattr(item, "description", "") or "").lower()
        title = str(getattr(item, "title", "") or "").lower()
        for term in terms:
            hit = False
            if term in desc:
                points += 4 + min(len(term), 6)
                hit = True
            elif term in title:
                points += 3 + min(len(term), 4)
                hit = True
            elif term in hay:
                points += 2
                hit = True
            if hit and term in distinctive:
                hit_distinct += 1
        if query in hay:
            points += 8
            # 整句命中视为足够特色
            hit_distinct = max(hit_distinct, 1)
        if distinctive and hit_distinct <= 0:
            return 0, 0
        return points, hit_distinct

    ranked = [(score(item), item) for item in items]
    ranked = [((s, d), item) for (s, d), item in ranked if s > 0]
    ranked.sort(key=lambda pair: (-pair[0][0], -pair[0][1], pair[1].title or pair[1].id))
    return [item for _, item in ranked]


def _blueprint_match_llm_enabled() -> bool:
    from report_writing.infra.config_settings import blueprint_match_llm_enabled

    return blueprint_match_llm_enabled()


def match_blueprints_with_agent(
    home: Path,
    *,
    message: str,
    robot_id: str = "",
    invoke,
    limit: int = 8,
) -> tuple[str, list]:
    """Agent：阅读蓝本用途说明等字段，按用户需求推荐并返回可点选列表。"""
    catalog = search_blueprints(home, q="", robot_id=robot_id)
    if not catalog:
        return "", []

    # 先关键词收窄，再交给模型精排；无关键词命中则全量候选（截断）
    keyed = search_blueprints(home, q=message, robot_id=robot_id)
    if not _blueprint_match_llm_enabled():
        picked = (keyed or [])[:limit]
        if picked:
            reply = f"我按关键词找了 {len(picked)} 份可能相关的，你看看合不合适："
            return reply, picked
        return "", []

    pool = keyed or catalog
    pool = pool[:30]

    rows = []
    for item in pool:
        rows.append(
            {
                "id": item.id,
                "title": item.title or item.id,
                "category": item.category or "",
                "tags": list(item.tags or []),
                "description": item.description or "",
                "chapter_count": int(item.chapter_count or 0),
                "outline_confirmed": bool(item.outline_confirmed),
            }
        )

    system = (
        "你是帮用户挑蓝本的搭档。根据用户需求，从候选蓝本中选出最相关的若干份。\n"
        "必须优先依据 description（用途说明），其次 title / category / tags。\n"
        "例如用户要写「安全预评价报告」，应匹配 description 含「安全预评价」的蓝本。\n"
        "reply 要像真人说话：一两句、口语化，说明为什么推荐；"
        "无论一份还是多份，都请用户点「选用」确认，不要声称已经帮用户订上。\n"
        "不要公文腔，不要罗列字段名。\n"
        "只返回 JSON 对象，不要 Markdown 代码块：\n"
        '{"reply":"口语化推荐说明（请用户点选）","ids":["蓝本id按相关度排序"]}\n'
        "若都不合适：ids 必须为空数组，reply 固定为简短一句「没有特别对口的」，"
        "【禁止】在 reply 中列举、暗示任何候选蓝本的标题或类型（用户界面不会展示未选中的蓝本）。"
    )
    user = (
        f"用户需求：{(message or '').strip()[:2000]}\n\n"
        f"候选蓝本（JSON 数组）：\n{json.dumps(rows, ensure_ascii=False)}"
    )
    raw = ""
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        # 模型失败时退回关键词结果
        return ("我先按用途和关键词帮你筛了几份，用不用你定——点「选用」确认：", (keyed or catalog)[:limit])

    payload = _parse_match_json(raw)
    # 模型明确返回 ids（含空数组）→ 尊重结果，空则不兜底罗列
    if isinstance(payload, dict) and "ids" in payload:
        ids = [str(x).strip() for x in (payload.get("ids") or []) if str(x).strip()]
        by_id = {item.id: item for item in pool}
        picked = [by_id[i] for i in ids if i in by_id]
        reply = str(payload.get("reply") or "").strip()
        if picked:
            reply = reply or f"我觉得这 {len(picked)} 份比较靠谱，用不用你来定——点「选用」确认："
            return reply, picked[:limit]
        # 无选中：不向对话层传递子模型文案（易泄露候选蓝本名）
        return "", []

    # 解析失败：再退回关键词命中（仍不退全量 catalog，避免「不符合也列一堆」）
    picked = (keyed or [])[:limit]
    reply = "我先按关键词帮你筛了几份，用不用你定——点「选用」确认：" if picked else (
        "没筛到对口蓝本。你可以再描述一下用途，或跳过蓝本自己定大纲。"
    )
    return reply, picked


def _parse_match_json(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def list_render_templates(home: Path | None = None) -> list[RenderTemplateSummary]:
    """优先磁盘模版；若未传 home 则回落演示常量（兼容旧调用）。"""
    if home is not None:
        from report_writing.services.render import list_templates

        return list_templates(Path(home))
    return [RenderTemplateSummary.from_dict(item) for item in RENDER_TEMPLATES]


def list_render_templates_for_robot(home: Path, robot_id: str) -> list[RenderTemplateSummary]:
    return scoped_render_templates(home, robot_id)


def list_word_styles_for_robot(home: Path, robot_id: str):
    return scoped_word_styles(home, robot_id)


def chat(home: Path, body: ChatRequest, *, invoke) -> ChatResponse:
    """兼容入口：委托有状态编排器。"""
    from report_writing.chat_orchestrator import handle_chat

    return handle_chat(home, body, invoke=invoke)


def generate_prompts(body: GenerateRobotPromptsRequest, *, invoke) -> GenerateRobotPromptsResponse:
    """根据用户描述生成 chat / study / write 系统提示词。"""
    text = (body.text or "").strip()
    if not text:
        raise ValueError("请先输入需求说明")
    kind = (body.kind or "all").strip().lower()
    if kind not in {"all", "chat", "study", "write"}:
        raise ValueError("kind 只能是 all / chat / study / write")
    name = (body.name or "").strip() or "未命名机器人"
    description = (body.description or "").strip() or "（无）"
    want = ["chat", "study", "write"] if kind == "all" else [kind]
    system = (
        "你是提示词工程师。根据用户对书写机器人的需求说明，生成可直接作为系统提示词的中文文本。\n"
        "要求：具体可执行；面向报告书写场景；不要输出 Markdown 代码块或解释。\n"
        "只返回 JSON 对象，字段仅包含需要的键：chat_prompt / study_prompt / write_prompt。\n"
        "字段含义：\n"
        "- chat_prompt：对话人格与边界；须写明「像真人同事、口语化、先接住再指路」，以及如何引用蓝本/知识库、禁止编造项目事实\n"
        "- study_prompt：分析样板要点时的系统指令（如何精确归纳写法要点、表格列/固定项与可变项、检索词；禁止固化项目专名事实）\n"
        "- write_prompt：写章节时的系统指令（如何组织 HTML 正文、表格版式）；须写明正文禁止（来源：…）/资料缺口/【待补】，证据用 evidence_ids，缺口只进 uncovered_points\n"
    )
    user = (
        f"机器人名称：{name}\n"
        f"机器人说明：{description}\n"
        f"需要生成的字段：{', '.join(want)}\n"
        f"用户需求说明：\n{text[:6000]}"
    )
    raw = str(invoke(system, user) or "").strip()
    payload = _parse_prompt_json(raw)
    result = GenerateRobotPromptsResponse()
    if "chat" in want:
        result.chat_prompt = str(payload.get("chat_prompt") or "").strip()
    if "study" in want:
        result.study_prompt = str(payload.get("study_prompt") or "").strip()
    if "write" in want:
        result.write_prompt = str(payload.get("write_prompt") or "").strip()
    if kind == "all" and not (result.chat_prompt or result.study_prompt or result.write_prompt):
        # 模型偶发只返回一段文本：拆成 chat
        result.chat_prompt = raw[:4000]
    elif kind != "all" and not getattr(result, f"{kind}_prompt"):
        setattr(result, f"{kind}_prompt", raw[:4000])
    return result


def _parse_prompt_json(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip())
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


RENDER_TEMPLATES = [
    {
        "id": "1",
        "name": "安评正文 A4",
        "desc": "标准正文排版，适合安全预评价报告。",
        "format": "DOCX",
        "updated": "最近编辑",
        "tone": "linear-gradient(145deg,#dbeafe,#eff6ff)",
        "margin": "上下 2.54cm / 左右 3.17cm",
        "font": "宋体 12pt",
        "heading": "黑体分级标题",
        "header": "报告名称 + 页码",
    },
    {
        "id": "2",
        "name": "环评简报",
        "desc": "偏短篇排版，适合摘要与结论页。",
        "format": "PDF",
        "updated": "演示数据",
        "tone": "linear-gradient(145deg,#dcfce7,#f0fdf4)",
        "margin": "上下 2.0cm / 左右 2.5cm",
        "font": "微软雅黑 11pt",
        "heading": "加粗无序号",
        "header": "仅页码",
    },
    {
        "id": "3",
        "name": "表格附件册",
        "desc": "宽表友好，适合监测数据附录。",
        "format": "DOCX",
        "updated": "演示数据",
        "tone": "linear-gradient(145deg,#fef3c7,#fffbeb)",
        "margin": "上下 1.5cm / 左右 1.8cm",
        "font": "宋体 10.5pt",
        "heading": "表题居中",
        "header": "附件编号",
    },
]


def _summary(robot: Robot) -> RobotSummary:
    return RobotSummary(
        id=robot.id,
        name=robot.name,
        description=robot.description,
        blueprint_count=len(robot.blueprint_ids),
        render_template_count=len(robot.render_template_ids),
        word_style_count=len(getattr(robot, "word_style_ids", None) or []),
        knowledge_folder_count=len(robot.knowledge_folder_ids),
        element_library_count=len(getattr(robot, "element_library_ids", None) or []),
        materials_pack_count=len(getattr(robot, "materials_pack_ids", None) or []),
        updated_at=robot.updated_at,
    )


def _persist_robot(home: Path, robot: Robot) -> None:
    if _use_mysql():
        _db_save_robot(robot)
        return
    _file_save_robot(home, robot)


def _file_save_robot(home: Path, robot: Robot) -> None:
    path = robots_root(home) / f"{robot.id}.json"
    path.write_text(json.dumps(robot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _file_get_robot(home: Path, robot_id: str) -> Robot | None:
    path = robots_root(home) / f"{robot_id}.json"
    if not path.is_file():
        return None
    return _load_path(path)


def _load_path(path: Path) -> Robot:
    return Robot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _ensure_robot_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    from report_writing.infra.json_tables import migrate_legacy_document_column, table_columns

    connection.execute(_ROBOT_CHILD_DDL)
    cols = table_columns(connection, _ROBOT_PARENT)
    if not cols:
        connection.execute(_ROBOT_META_DDL)
        _schema_ready = True
        return
    if "name" not in cols:
        for stmt in (
            "ADD COLUMN name varchar(255) NOT NULL DEFAULT ''",
            "ADD COLUMN description text",
        ):
            try:
                connection.execute(f"ALTER TABLE {_ROBOT_PARENT} {stmt}")
            except Exception:
                pass

    def _on_migrate(parent_id: str, doc: dict, updated_at: str) -> None:
        try:
            robot = Robot.from_dict(doc)
        except Exception:
            return
        created = robot.created_at or updated_at or _now()
        updated = updated_at or robot.updated_at or created
        connection.execute(
            f"""
            INSERT INTO {_ROBOT_PARENT} (id, name, description, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                description = incoming.description,
                updated_at = incoming.updated_at
            """,
            (
                robot.id,
                robot.name or "",
                robot.description or "",
                created,
                updated,
            ),
        )

    migrate_legacy_document_column(connection, _ROBOT_PARENT, _ROBOT_CHILD, "robot_id", on_row=_on_migrate)
    from report_writing.infra.json_tables import drop_legacy_document_column

    drop_legacy_document_column(connection, _ROBOT_PARENT)
    _schema_ready = True


def _use_mysql() -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings, mysql_available

    if not mysql_available():
        return False
    if _schema_ready:
        return True
    settings = load_database_settings()
    if settings is None:
        return False
    try:
        with _connect(settings) as connection:
            _ensure_robot_schema(connection)
        return True
    except Exception as error:
        import logging

        logging.getLogger(__name__).warning("机器人改用本地 JSON 存储：%s", error)
        return False


def sync_robots_from_disk(home: Path) -> int:
    """将 data/robots 下 JSON  upsert 到 MySQL（磁盘较新或库中不存在时写入）。"""
    if not _use_mysql():
        return 0
    saved = 0
    for path in robots_root(home).glob("*.json"):
        try:
            robot = _load_path(path)
        except Exception:
            continue
        try:
            existing = _db_get_robot(robot.id)
            disk_ts = robot.updated_at or robot.created_at or ""
            db_ts = (existing.updated_at or existing.created_at or "") if existing else ""
            if existing is None or disk_ts >= db_ts:
                _db_save_robot(robot)
                saved += 1
        except Exception:
            continue
    return saved


def _db_list_robots(user_id: str) -> list[RobotSummary]:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings
    from report_writing.services.users import owns_user_resource

    settings = load_database_settings()
    if settings is None:
        return []
    items: list[RobotSummary] = []
    with _connect(settings) as connection:
        _ensure_robot_schema(connection)
        id_rows = connection.execute(
            f"SELECT id FROM {_ROBOT_PARENT} ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
        for id_row in id_rows or []:
            rid = str(id_row[0])
            doc = fetch_document_child(connection, _ROBOT_CHILD, "robot_id", rid)
            if doc is None:
                continue
            try:
                robot = Robot.from_dict(doc)
            except Exception:
                continue
            if not owns_user_resource(user_id, getattr(robot, "user_id", "") or ""):
                continue
            items.append(_summary(robot))
    return items


def _db_get_robot(robot_id: str) -> Robot | None:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return None
    with _connect(settings) as connection:
        _ensure_robot_schema(connection)
        doc = fetch_document_child(connection, _ROBOT_CHILD, "robot_id", robot_id)
        if doc is None:
            return None
        robot = Robot.from_dict(doc)
        from report_writing.services.users import current_user_id, owns_user_resource

        owner = (getattr(robot, "user_id", "") or "").strip() or "default"
        if not owns_user_resource(current_user_id(), owner):
            return None
        return robot


def _db_save_robot(robot: Robot) -> None:
    from report_writing.infra.json_tables import upsert_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        raise RuntimeError("未配置数据库")
    created = robot.created_at or robot.updated_at or _now()
    updated = robot.updated_at or _now()
    with _connect(settings) as connection:
        _ensure_robot_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_ROBOT_PARENT} (id, name, description, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                description = incoming.description,
                updated_at = incoming.updated_at
            """,
            (robot.id, robot.name or "", robot.description or "", created, updated),
        )
        upsert_document_child(connection, _ROBOT_CHILD, "robot_id", robot.id, robot.to_dict())


def _db_delete_robot(robot_id: str) -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return False
    with _connect(settings) as connection:
        _ensure_robot_schema(connection)
        exists = connection.execute(
            f"SELECT 1 FROM {_ROBOT_PARENT} WHERE id = %s",
            (robot_id,),
        ).fetchone()
        if exists is None:
            return False
        connection.execute(f"DELETE FROM {_ROBOT_CHILD} WHERE robot_id = %s", (robot_id,))
        connection.execute(f"DELETE FROM {_ROBOT_PARENT} WHERE id = %s", (robot_id,))
    return True


def _document_dict(payload) -> dict:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("document 格式无效")
    return payload


def _require_id(robot_id: str) -> str:
    value = str(robot_id or "").strip()
    if not _ID_SAFE.match(value):
        raise ValueError("机器人编号不合法")
    return value


def _str_list(raw) -> list[str]:
    if not raw:
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
