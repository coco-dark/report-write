/** 对话富文本：转义、Markdown → HTML、消毒展示。 */

const ALLOWED = new Set([
  "A",
  "B",
  "BLOCKQUOTE",
  "BR",
  "CODE",
  "DIV",
  "EM",
  "H1",
  "H2",
  "H3",
  "H4",
  "HR",
  "I",
  "LI",
  "OL",
  "P",
  "PRE",
  "SPAN",
  "STRONG",
  "TABLE",
  "TBODY",
  "TD",
  "TH",
  "THEAD",
  "TR",
  "U",
  "UL",
]);

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function looksLikeHtml(text) {
  const raw = String(text || "").trim();
  // 避免把 Markdown 里的 `<占位>` 误判成 HTML；需有真实闭合标签或常见块标签
  return /<\/(?:p|div|br|strong|em|ul|ol|li|h[1-6]|table|tr|td|th|pre|code|hr)\s*>/i.test(raw)
    || /<(?:p|div|br|strong|em|ul|ol|li|h[1-6]|table|tr|td|th|pre|code|hr)\b[^>]*>/i.test(raw);
}

function inlineFormat(escaped) {
  let html = escaped;
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  html = html.replace(/_([^_\n]+)_/g, "<em>$1</em>");
  return html;
}

/** 纯文本 / Markdown → 可安全展示的 HTML。 */
export function plainToHtml(text) {
  const raw = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!raw.trim()) return "";

  const lines = raw.split("\n");
  const blocks = [];
  let list = null;
  let inCode = false;
  let codeLines = [];

  const closeList = () => {
    if (list) {
      blocks.push(`</${list.tag}>`);
      list = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      if (inCode) {
        blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      closeList();
      blocks.push("<hr>");
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      blocks.push(`<h${level}>${inlineFormat(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }

    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
      closeList();
      blocks.push(`<blockquote><p>${inlineFormat(escapeHtml(quote[1]))}</p></blockquote>`);
      continue;
    }

    const bullet = line.match(/^\s*[-•*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      const body = inlineFormat(escapeHtml(bullet ? bullet[1] : numbered[1]));
      if (!list || list.tag !== tag) {
        closeList();
        list = { tag };
        blocks.push(`<${tag}>`);
      }
      blocks.push(`<li>${body}</li>`);
      continue;
    }

    closeList();
    if (!trimmed) {
      blocks.push("<br>");
    } else {
      blocks.push(`<p>${inlineFormat(escapeHtml(line))}</p>`);
    }
  }

  if (inCode) {
    blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  closeList();
  return blocks.join("");
}

/** 去掉危险标签/属性，保留常用排版。 */
export function sanitizeHtml(html) {
  const source = String(html || "").trim();
  if (!source) return "";
  const doc = new DOMParser().parseFromString(`<div class="root">${source}</div>`, "text/html");
  const root = doc.body.querySelector(".root") || doc.body;
  const walk = (node) => {
    const children = [...node.childNodes];
    for (const child of children) {
      if (child.nodeType === Node.TEXT_NODE) continue;
      if (child.nodeType !== Node.ELEMENT_NODE) {
        child.remove();
        continue;
      }
      const tag = child.tagName.toUpperCase();
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "IFRAME" || tag === "OBJECT") {
        child.remove();
        continue;
      }
      if (!ALLOWED.has(tag)) {
        const parent = child.parentNode;
        while (child.firstChild) parent.insertBefore(child.firstChild, child);
        child.remove();
        continue;
      }
      [...child.attributes].forEach((attr) => {
        const name = attr.name.toLowerCase();
        if (name.startsWith("on") || name === "style" || name === "srcdoc") {
          child.removeAttribute(attr.name);
          return;
        }
        if (tag === "A" && name === "href") {
          const href = String(attr.value || "").trim();
          if (/^\s*javascript:/i.test(href)) child.removeAttribute("href");
          else {
            child.setAttribute("rel", "noopener noreferrer");
            child.setAttribute("target", "_blank");
          }
          return;
        }
        if (!["href", "title", "colspan", "rowspan"].includes(name)) {
          child.removeAttribute(attr.name);
        }
      });
      walk(child);
    }
  };
  walk(root);
  return root.innerHTML;
}

/** 气泡展示：已是 HTML 则消毒，否则按 Markdown 渲染。 */
export function toDisplayHtml(text) {
  const raw = String(text ?? "");
  if (!raw.trim()) return "";
  if (looksLikeHtml(raw)) return sanitizeHtml(raw);
  return plainToHtml(raw);
}

export function htmlToPlain(html) {
  const box = document.createElement("div");
  box.innerHTML = String(html || "");
  return (box.innerText || box.textContent || "")
    .replace(/\u00a0/g, " ")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .trim();
}

function looksLikeWordHtml(html) {
  return /mso-|Mso|supportLists|xmlns:w=/i.test(html || "");
}

function firstLineSequence(text) {
  const line = (text || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
  const match = line.match(/^(\d+(?:\.\d+)*)/);
  return match ? match[1] : "";
}

/** Word 复制片段时 text/plain 常从 1 重排；HTML 的 supportLists / mso-list 仍带屏幕序号。 */
export function wordHtmlToPlain(html) {
  if (!html) return "";
  let text = html;
  const markerText = (inner) => {
    const tmp = document.createElement("div");
    tmp.innerHTML = inner || "";
    let plain = (tmp.textContent || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    if (!plain) return "";
    const match = plain.match(/^(\d+(?:\.\d+)*\.?|[一二三四五六七八九十百]+[、.]?)\s*/);
    if (match) return `${match[1].replace(/\.$/, "")} `;
    return `${plain} `;
  };
  text = text.replace(/<!--\[if\s*!supportLists\]-->([\s\S]*?)<!--\[endif\]-->/gi, (_, inner) =>
    markerText(inner)
  );
  text = text.replace(/<span\b[^>]*mso-list\s*:\s*Ignore[^>]*>([\s\S]*?)<\/span>/gi, (_, inner) =>
    markerText(inner)
  );
  text = text
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|h[1-6]|li|tr)\s*>/gi, "\n")
    .replace(/<\/ol\s*>/gi, "\n")
    .replace(/<\/ul\s*>/gi, "\n");
  const box = document.createElement("div");
  box.innerHTML = text;
  return (box.innerText || box.textContent || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

/** 粘贴原文分析：优先保留 Word HTML 中的真实序号。 */
export function recoverClipboardPlain(event) {
  const plain = (event?.clipboardData?.getData("text/plain") || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
  const html = event?.clipboardData?.getData("text/html") || "";
  if (looksLikeWordHtml(html)) {
    const fromWord = wordHtmlToPlain(html);
    if (fromWord.trim()) {
      const plainSeq = firstLineSequence(plain);
      const wordSeq = firstLineSequence(fromWord);
      if (!plain.trim()) return fromWord;
      if (wordSeq && plainSeq === "1" && wordSeq !== "1") return fromWord;
      if (wordSeq && wordSeq.includes(".") && !plainSeq.includes(".")) return fromWord;
      if (wordSeq && Number(wordSeq.split(".")[0]) > Number((plainSeq || "0").split(".")[0])) {
        return fromWord;
      }
      if (wordSeq) return fromWord;
    }
  }
  if (plain.trim()) return plain.trim();
  if (html.trim()) return wordHtmlToPlain(html);
  return "";
}

/** 粘贴：优先保留安全 HTML，否则纯文本。 */
export function clipboardToHtml(event) {
  const html = event.clipboardData?.getData("text/html") || "";
  const plain = event.clipboardData?.getData("text/plain") || "";
  if (html.trim() && /<[a-z][\s\S]*>/i.test(html)) {
    return sanitizeHtml(html) || escapeHtml(plain).replace(/\n/g, "<br>");
  }
  if (plain) return escapeHtml(plain).replace(/\n/g, "<br>");
  return "";
}

export function insertHtmlAtCursor(root, html) {
  root.focus();
  const selection = window.getSelection();
  if (!selection || !selection.rangeCount || !root.contains(selection.anchorNode)) {
    root.innerHTML = `${root.innerHTML || ""}${html}`;
    return;
  }
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const temp = document.createElement("div");
  temp.innerHTML = html;
  const frag = document.createDocumentFragment();
  let node;
  let last = null;
  while ((node = temp.firstChild)) {
    last = frag.appendChild(node);
  }
  range.insertNode(frag);
  if (last) {
    range.setStartAfter(last);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
  }
}
