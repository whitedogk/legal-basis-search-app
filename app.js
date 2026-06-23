const data = window.APP_DATA || { items: [], source: "" };
const queryInput = document.querySelector("#query");
const resultsEl = document.querySelector("#results");
const emptyEl = document.querySelector("#empty");
const resultCountEl = document.querySelector("#result-count");
const sourceNameEl = document.querySelector("#source-name");
const explanationEl = document.querySelector("#explanation");
const template = document.querySelector("#result-template");
const filters = [...document.querySelectorAll(".filter")];

let activeFilter = "all";
let selectedId = null;
sourceNameEl.textContent = data.sources?.length ? data.sources.join(" / ") : data.source;
data.items.forEach((item, index) => {
  item.viewId = String(index);
});
queryInput.value = new URLSearchParams(window.location.search).get("q") || "";

const normalize = (value) =>
  (value || "")
    .toString()
    .toLowerCase()
    .replace(/[０-９]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0))
    .replace(/\s+/g, " ")
    .trim();

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const escapeHtml = (value) =>
  (value || "")
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const highlight = (text, terms) => {
  let output = escapeHtml(text);
  for (const term of terms) {
    if (!term) continue;
    output = output.replace(new RegExp(`(${escapeRegExp(term)})`, "gi"), "<mark>$1</mark>");
  }
  return output;
};

const hasFilter = (item) => {
  const refs = item.references || [];
  if (activeFilter === "act") return item.sourceType === "law";
  if (activeFilter === "case") return item.sourceType === "case";
  if (activeFilter === "record") return item.sourceType === "record";
  if (activeFilter === "law") return refs.some((ref) => /法第|生活保護法|福祉法|施行規則|法律|告示|別表/.test(ref));
  if (activeFilter === "notice") return refs.some((ref) => /通知|社発/.test(ref));
  if (activeFilter === "qa") return refs.some((ref) => /問答/.test(ref));
  return true;
};

const scoreItem = (item, terms) => {
  const title = normalize(item.title);
  const chapter = normalize(item.chapter);
  const sourceType = normalize(item.sourceType);
  const refs = normalize((item.references || []).join(" "));
  const body = normalize(item.body);
  let score = 0;
  for (const term of terms) {
    if (sourceType.includes(term)) score += 4;
    if (title.includes(term)) score += 16;
    if (refs.includes(term)) score += 12;
    if (chapter.includes(term)) score += 6;
    if (body.includes(term)) score += 2;
  }
  return score;
};

const makeSnippet = (item, rawTerms) => {
  const body = item.body || "";
  const normalizedBody = normalize(body);
  const normalizedTerms = rawTerms.map(normalize);
  let index = -1;
  for (const term of normalizedTerms) {
    index = normalizedBody.indexOf(term);
    if (index >= 0) break;
  }
  const start = Math.max(0, index - 80);
  const snippet = body.slice(start, start + 240);
  return `${start > 0 ? "…" : ""}${snippet}${start + 240 < body.length ? "…" : ""}`;
};

const refClass = (ref) => (/法第|生活保護法|福祉法|施行規則|法律|告示|別表/.test(ref) ? "law" : "");
const displayRef = (ref) => {
  let output = normalizeSpaces(ref).replace(/－ \(/g, "－(");
  if (/\([^)]$/.test(output) || /\([^)]+$/.test(output)) {
    output = `${output})`;
  }
  return output;
};

const sourceLabel = (item) => {
  if (item.sourceType === "law") return `${item.chapter} / e-Gov法令`;
  if (item.sourceType === "record") return `${item.chapter || "調書記録"} / 調書記録事例`;
  return item.chapter || "章未分類";
};

const referenceHeading = (item) => (item.sourceType === "record" ? "関連根拠候補" : "根拠法令・通知");

const plainText = (value) => normalizeSpaces((value || "").replace(/[「」『』]/g, ""));

const normalizeSpaces = (value) => value.toString().replace(/\s+/g, " ").trim();

const readableSentence = (sentence) => {
  const cleaned = plainText(sentence);
  return cleaned.length > 120 ? `${cleaned.slice(0, 118)}…` : cleaned;
};

const splitSentences = (body) =>
  body
    .replace(/\n/g, "")
    .split("。")
    .map(readableSentence)
    .filter(
      (sentence) =>
        sentence.length > 18 &&
        !/^(問|答|なお、?設問|根拠|参考|参照)/.test(sentence) &&
        !/(どうなるか|よいか|示されたい|説明されたい|取り扱うのか|するのか|すべきか|なるのか|できるか|あるか)$/.test(sentence),
    )
    .slice(0, 14);

const sentenceScore = (sentence, index) => {
  let score = Math.max(0, 8 - index);
  if (/である|となる|必要|要する|認め|扱|判断|留意|注意|支給|認定|適用/.test(sentence)) score += 8;
  if (/どうなるか|よいか|されたいか|この場合/.test(sentence)) score -= 8;
  if (/^\s*（/.test(sentence)) score -= 3;
  return score;
};

const makeSummary = (item) => {
  const sentences = splitSentences(item.body || "");
  const refs = item.references?.length ? item.references : [];
  const selected = sentences
    .map((sentence, index) => ({ sentence, index, score: sentenceScore(sentence, index) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)
    .sort((a, b) => a.index - b.index)
    .map(({ sentence }) => sentence);
  const summary = selected.length
    ? `${selected.join("。")}。`
    : "本文から要約できる十分な文章を抽出できませんでした。本文を確認してください。";
  return {
    summary,
    refs,
    note: "要約は本文から自動作成しています。最終判断では、下の本文と根拠法令・通知を確認してください。",
  };
};

const renderExplanation = (item) => {
  const summary = makeSummary(item);
  explanationEl.innerHTML = `
    <p class="eyebrow">${escapeHtml(sourceLabel(item))}</p>
    <h2>${escapeHtml(item.title)}</h2>
    <div class="explain-group">
      <h3>${item.sourceType === "record" ? "記録要約" : "本文要約"}</h3>
      <p class="explain-text">${escapeHtml(summary.summary)}</p>
    </div>
    <div class="explain-group">
      <h3>${escapeHtml(referenceHeading(item))}</h3>
      <div class="refs">
        ${
          summary.refs.length
            ? summary.refs
                .map((ref) => `<span class="ref ${refClass(ref)}">${escapeHtml(displayRef(ref))}</span>`)
                .join("")
            : '<span class="ref">本文中に明示なし</span>'
        }
      </div>
    </div>
    <div class="explain-group">
      <h3>本文</h3>
      <p class="explain-body">${escapeHtml(item.body)}</p>
      ${
        item.sourceUrl
          ? `<p class="explain-text"><a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noreferrer">e-Govで原文を開く</a></p>`
          : ""
      }
      <p class="explain-text">${escapeHtml(summary.note)}</p>
    </div>
  `;
};

const render = () => {
  const rawQuery = queryInput.value.trim();
  const rawTerms = rawQuery.split(/[ 　]+/).filter(Boolean);
  const terms = rawTerms.map(normalize);
  const matches = rawQuery
    ? data.items
        .map((item) => ({ item, score: scoreItem(item, terms) }))
        .filter(({ item, score }) => score > 0 && hasFilter(item))
        .sort((a, b) => b.score - a.score)
        .slice(0, 80)
    : [];

  resultCountEl.textContent = `${matches.length}件`;
  resultsEl.replaceChildren();
  emptyEl.hidden = matches.length > 0 || rawQuery;
  if (matches.length === 0 && rawQuery) {
    emptyEl.hidden = false;
    emptyEl.querySelector("h2").textContent = "該当する候補がありません";
    emptyEl.querySelector("p").textContent = "別の表記や短い単語で検索してみてください。";
  } else if (!rawQuery) {
    emptyEl.querySelector("h2").textContent = "関連ワードを入力してください";
    emptyEl.querySelector("p").textContent = "問のタイトル、本文、根拠候補をまとめて検索します。";
  }

  for (const { item } of matches) {
    const node = template.content.cloneNode(true);
    const card = node.querySelector(".result-card");
    card.dataset.id = item.viewId;
    if (item.viewId === selectedId) {
      card.classList.add("selected");
    }
    node.querySelector(".chapter").textContent = sourceLabel(item);
    node.querySelector("h2").innerHTML = highlight(item.title, rawTerms);
    node.querySelector(".snippet").innerHTML = highlight(makeSnippet(item, rawTerms), rawTerms);
    node.querySelector(".body").innerHTML = highlight(item.body, rawTerms);
    node.querySelector(".ref-label").textContent = referenceHeading(item);
    const refsEl = node.querySelector(".refs");
    const refs = item.references?.length ? item.references : ["本文中に明示なし"];
    refs.forEach((ref) => {
      const chip = document.createElement("span");
      chip.className = `ref ${refClass(ref)}`;
      chip.textContent = displayRef(ref);
      refsEl.append(chip);
    });
    node.querySelector(".explain-button").addEventListener("click", () => {
      selectedId = item.viewId;
      renderExplanation(item);
      document.querySelectorAll(".result-card").forEach((cardEl) => {
        cardEl.classList.toggle("selected", cardEl.dataset.id === selectedId);
      });
    });
    resultsEl.append(node);
  }
};

queryInput.addEventListener("input", render);
filters.forEach((button) => {
  button.addEventListener("click", () => {
    filters.forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeFilter = button.dataset.filter;
    render();
  });
});

render();
