/**
 * 章/要点要素关联：飞行中标记。
 * pending 仅在 PUT/DELETE 未结束前用于乐观回显；落盘成功后必须以服务端章节为准并清 pending。
 */

const inFlight = new Set();

export function beginChapterBindingFlight(chapterKey) {
  const key = String(chapterKey || "").trim();
  if (key) inFlight.add(key);
}

export function endChapterBindingFlight(chapterKey) {
  const key = String(chapterKey || "").trim();
  if (key) inFlight.delete(key);
}

export function chapterBindingInFlight(chapterKey) {
  return inFlight.has(String(chapterKey || "").trim());
}

export function clearAllBindingFlights() {
  inFlight.clear();
}
