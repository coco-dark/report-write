export const BIZ_TYPES = [
  { value: "001", label: "001 电力勘测设计企业电力安全生产标准化复审报告" },
  { value: "002", label: "002 环境检测报告" },
  { value: "003", label: "003 输变电工程安全预评价报告" },
  { value: "004", label: "004 输变电工程水土保持方案报告书" },
  { value: "006", label: "006 输变电工程环境影响报告" },
];

export const STATUS_LABELS = {
  pending: "待处理",
  searching: "检索中",
  writing: "写作中",
  checking: "检查中",
  awaiting_confirm: "已写作",
  confirmed: "已写作",
  needs_human: "已写作",
  skipped: "已跳过",
  studying: "待开写",
  running: "进行中",
  completed: "已完成",
  done: "完成",
  accept: "通过",
  revise: "退回修改",
  approve: "通过",
  edit: "改字后通过",
  return: "退回",
  skip: "跳过",
};

export const STEP_NAMES = {
  upload: "上传",
  study: "分析",
  search: "检索",
  write: "写作",
  check: "检查",
};

export const STEP_STATUS = {
  running: "执行中",
  done: "完成",
  skipped: "跳过",
};

/** Ant Design Tag color（兼容旧 Element type 语义） */
export function statusTagType(status) {
  if (status === "confirmed" || status === "completed" || status === "done" || status === "accept") return "success";
  if (status === "needs_human" || status === "awaiting_confirm" || status === "running" || status === "revise") return "warning";
  if (status === "skipped") return "default";
  return "default";
}

export function newTaskId() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `w${stamp}${Math.random().toString(36).slice(2, 6)}`;
}

export function formatExtracts(items) {
  return (items || [])
    .map((item) => [item.name, item.kind || "文本", item.required === false ? "可空" : "必填", item.style || "归纳"].join("｜"))
    .join("\n");
}

export function parseExtracts(value) {
  return lines(value).map((line) => {
    const [name, kind, required, style] = line.split(/[|｜]/).map((part) => part.trim());
    return {
      name,
      kind: kind || "文本",
      source: "",
      hint: name,
      required: required !== "可空",
      style: style || "归纳",
    };
  }).filter((item) => item.name);
}

export function lines(value) {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}
