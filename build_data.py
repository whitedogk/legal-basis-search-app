from __future__ import annotations

import json
import re
import struct
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


SOURCE_DOC = Path(r"D:\document\ドーナドーナ\R6運用事例集全体版(目次付き).doc")
TEXT_OUT = Path("extracted.txt")
DATA_OUT = Path("app-data.js")
LIFE_PROTECTION_LAW_ID = "325AC0000000144"
LIFE_PROTECTION_API_URL = f"https://laws.e-gov.go.jp/api/1/lawdata/{LIFE_PROTECTION_LAW_ID}"
LIFE_PROTECTION_XML = Path("life_protection_act.xml")

END_OF_CHAIN = 0xFFFFFFFE
FREE_SECTOR = 0xFFFFFFFF
FAT_SECTOR = 0xFFFFFFFD


def read_compound_streams(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
    fat_count = struct.unpack_from("<I", data, 44)[0]
    first_dir_sector = struct.unpack_from("<I", data, 48)[0]
    difat = struct.unpack_from("<109I", data, 76)
    fat_sectors = [item for item in difat if item not in (FREE_SECTOR, END_OF_CHAIN, FAT_SECTOR)]

    def sector_offset(sector: int) -> int:
        return (sector + 1) * sector_size

    fat: list[int] = []
    for sector in fat_sectors[:fat_count]:
        offset = sector_offset(sector)
        fat.extend(struct.unpack_from(f"<{sector_size // 4}I", data, offset))

    def sector_chain(start: int) -> list[int]:
        sectors: list[int] = []
        seen: set[int] = set()
        sector = start
        while sector not in (END_OF_CHAIN, FREE_SECTOR) and sector < len(fat) and sector not in seen:
            seen.add(sector)
            sectors.append(sector)
            sector = fat[sector]
        return sectors

    def read_chain(start: int, size: int | None = None) -> bytes:
        blob = b"".join(data[sector_offset(sector) : sector_offset(sector) + sector_size] for sector in sector_chain(start))
        return blob[:size] if size is not None else blob

    directory = read_chain(first_dir_sector)
    streams: dict[str, bytes] = {}
    for offset in range(0, len(directory), 128):
        entry = directory[offset : offset + 128]
        if len(entry) < 128:
            break
        name_len = struct.unpack_from("<H", entry, 64)[0]
        if name_len < 2:
            continue
        name = entry[: name_len - 2].decode("utf-16le", "ignore")
        entry_type = entry[66]
        start_sector = struct.unpack_from("<I", entry, 116)[0]
        size = struct.unpack_from("<Q", entry, 120)[0]
        if entry_type == 2 and size >= 4096:
            streams[name] = read_chain(start_sector, size)
    return streams


def extract_doc_text(path: Path) -> str:
    streams = read_compound_streams(path)
    word = streams["WordDocument"]
    table = streams.get("1Table") or streams.get("0Table")
    if table is None:
        raise RuntimeError("Word table stream was not found.")

    piece_table = None
    for offset in range(len(table) - 6):
        if table[offset] != 2:
            continue
        size = struct.unpack_from("<I", table, offset + 1)[0]
        if not (100 < size < 200_000 and size % 12 == 4 and offset + 5 + size <= len(table)):
            continue
        count = (size - 4) // 12
        cps = list(struct.unpack_from(f"<{count + 1}I", table, offset + 5))
        if cps and cps[0] == 0 and all(cps[index] <= cps[index + 1] for index in range(min(count, 20))):
            piece_table = (offset, count, cps)
    if piece_table is None:
        raise RuntimeError("Word piece table was not found.")

    offset, count, cps = piece_table
    pcd_offset = offset + 5 + 4 * (count + 1)
    parts: list[str] = []
    for index in range(count):
        fc_compressed = struct.unpack_from("<I", table, pcd_offset + index * 8 + 2)[0]
        compressed = bool(fc_compressed & 0x40000000)
        file_offset = fc_compressed & 0x3FFFFFFF
        start = file_offset // 2 if compressed else file_offset
        char_count = cps[index + 1] - cps[index]
        raw = word[start : start + (char_count if compressed else char_count * 2)]
        parts.append(raw.decode("cp932" if compressed else "utf-16le", "ignore"))

    text = "".join(parts)
    title_index = text.find("生 活 保 護 運 用 事 例 集")
    if title_index > 0:
        text = text[title_index:]
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f]", "", text)
    text = re.sub(r"\r+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


QUESTION_RE = re.compile(r"^（問[０-９0-9]+(?:－[０-９0-9]+)*）\s*(.+)$")
QUESTION_ANY_RE = re.compile(r"（問[０-９0-9]+(?:[－―ー-][０-９0-9]+)*）[ 　]*([^\n\t\r]{2,90})")
CHAPTER_RE = re.compile(r"^第[０-９0-9一二三四五六七八九十]+[ 　].+")
REFERENCE_RE = re.compile(
    r"(生活保護法|児童福祉法|老人福祉法|障害者総合支援法|感染症予防法|心神喪失者等医療観察法|"
    r"高齢者虐待.*法律|法第[０-９0-9]+条(?:の[０-９0-9]+)?|法施行規則第[０-９0-9]+条|"
    r"告示別表第[^、。\n）)]*|局第[０-９0-9]+[－ー―-][^、。\n）)]*|"
    r"局長通知第[^、。\n）)]*|課長通知第[^、。\n）)]*|次官通知第[^、。\n）)]*|"
    r"局長問答第[^、。\n）)]*|課長問答第[^、。\n）)]*|別冊問答集?[^、。\n）)]*|"
    r"昭和[０-９0-9]+年[^、。\n）)]*|平成[０-９0-9]+年[^、。\n）)]*|令和[０-９0-9]+年[^、。\n）)]*|"
    r"社発第[０-９0-9]+号|生活と福祉[^、。\n）)]*)"
)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_digits(text: str) -> str:
    return text.translate(str.maketrans("０１２３４５６７８９－―ー", "0123456789---"))


def kanji_number_to_int(value: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in digits:
        return digits[value]
    if "百" in value:
        before, after = value.split("百", 1)
        total = (digits.get(before, 1) if before else 1) * 100
        rest = kanji_number_to_int(after)
        return total + (rest or 0)
    if "十" in value:
        before, after = value.split("十", 1)
        total = (digits.get(before, 1) if before else 1) * 10
        rest = kanji_number_to_int(after)
        return total + (rest or 0)
    total = 0
    for char in value:
        if char not in digits:
            return None
        total = total * 10 + digits[char]
    return total


def article_number_variants(article_title: str) -> list[str]:
    match = re.search(r"第([一二三四五六七八九十百〇零0-9０-９]+)条(の([一二三四五六七八九十百〇零0-9０-９]+))?", article_title)
    if not match:
        return []
    main = kanji_number_to_int(normalize_digits(match.group(1)))
    branch = kanji_number_to_int(normalize_digits(match.group(3) or ""))
    if main is None:
        return []
    variants = [f"第{main}条", f"第{main}条の{branch}" if branch is not None else ""]
    return [variant for variant in variants if variant]


def find_references(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for match in REFERENCE_RE.finditer(text):
        ref = normalize_spaces(match.group(0))
        ref = ref.strip(" 　、。()（）")
        if len(ref) < 3 or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs[:18]


def strip_front_matter(text: str) -> str:
    """Drop the Word TOC and chapter guide before the first real Q&A item."""
    match = re.search(r"(?m)^（問１－１）", text)
    return text[match.start() :] if match else text


def infer_chapter(title: str) -> str:
    match = re.search(r"問([0-9]+)", normalize_digits(title))
    if not match:
        return "章未分類"
    return f"第{match.group(1)}"


def clean_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if (
            not line
            or "HYPERLINK" in line
            or "PAGEREF" in line
            or "MERGEFORMAT" in line
            or line == "目　次"
        ):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    guide_index = cleaned.find("この章で扱う事項")
    if guide_index >= 0:
        prefix = cleaned[:guide_index]
        chapter_index = prefix.rfind("\n第")
        cleaned = cleaned[: chapter_index if chapter_index >= 0 else guide_index].strip()
    for marker in ("\n参考資料", "\n生活保護関係主要判例", "\n海外渡航事例集"):
        marker_index = cleaned.find(marker)
        if marker_index >= 0:
            cleaned = cleaned[:marker_index].strip()
    reference_section = re.search(r"\n参\s*考\s*資\s*料", cleaned)
    if reference_section:
        cleaned = cleaned[: reference_section.start()].strip()
    return cleaned


def is_searchable_body(body: str) -> bool:
    if len(body) < 20:
        return False
    if "この章で扱う事項" in body or "HYPERLINK" in body or "PAGEREF" in body:
        return False
    return True


def build_items(text: str) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    text = strip_front_matter(text)

    matches = list(QUESTION_ANY_RE.finditer(text))
    for index, match in enumerate(matches):
        title = normalize_spaces(match.group(0))
        title = re.split(r"\s+PAGEREF|\s+HYPERLINK|", title)[0].strip()
        if (
            "参照" in title
            or "_Toc" in title
            or '"' in title
            or "INK" in title
            or "削除" in title
            or len(title) > 80
        ):
            continue
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = clean_body(text[match.end() : body_end])
        if not is_searchable_body(body):
            continue
        refs = find_references(body)
        item = {
            "id": title.split("）", 1)[0] + "）",
            "title": title,
            "chapter": infer_chapter(title),
            "sourceType": "case",
            "sourceUrl": "",
            "body": body,
            "references": refs,
        }
        quality = len(body) + len(refs) * 650 - body.count("TOC") * 1000
        key = str(item["id"])
        previous = candidates.get(key)
        if previous is None or quality > int(previous["_quality"]):
            item["_quality"] = quality
            candidates[key] = item

    items = []
    for item in candidates.values():
        item.pop("_quality", None)
        items.append(item)

    def sort_key(item: dict[str, object]) -> tuple[int, int, str]:
        normalized = normalize_digits(str(item["id"]))
        nums = [int(value) for value in re.findall(r"\d+", normalized)]
        return (nums[0] if nums else 999, nums[1] if len(nums) > 1 else 0, str(item["title"]))

    items.sort(key=sort_key)
    if items:
        return items

    lines = [line.strip() for line in text.splitlines()]
    fallback_items: list[dict[str, object]] = []
    current_chapter = ""
    current: dict[str, object] | None = None
    seen_titles: set[str] = set()

    for line in lines:
        if not line or "HYPERLINK" in line or "PAGEREF" in line or line == "目　次":
            continue
        chapter_match = CHAPTER_RE.match(line)
        if chapter_match and "章" not in line and len(line) < 40:
            current_chapter = line
            continue
        question_match = QUESTION_RE.match(line)
        if question_match:
            if current is not None:
                body = "\n".join(current.pop("_lines"))  # type: ignore[arg-type]
                current["body"] = body
                current["references"] = find_references(body)
                if current["title"] not in seen_titles and len(body) > 20:
                    seen_titles.add(str(current["title"]))
                    fallback_items.append(current)
            current = {
                "id": question_match.group(0).split("）", 1)[0] + "）",
                "title": question_match.group(0),
                "chapter": current_chapter,
                "_lines": [],
            }
            continue
        if current is not None:
            current["_lines"].append(line)  # type: ignore[index]

    if current is not None:
        body = "\n".join(current.pop("_lines"))  # type: ignore[arg-type]
        current["body"] = body
        current["references"] = find_references(body)
        if current["title"] not in seen_titles and len(body) > 20:
            fallback_items.append(current)

    return fallback_items


def fetch_life_protection_xml() -> str:
    request = urllib.request.Request(
        LIFE_PROTECTION_API_URL,
        headers={"User-Agent": "legal-basis-search-app/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            xml_text = response.read().decode("utf-8")
        LIFE_PROTECTION_XML.write_text(xml_text, encoding="utf-8")
        return xml_text
    except Exception:
        if LIFE_PROTECTION_XML.exists():
            return LIFE_PROTECTION_XML.read_text(encoding="utf-8")
        raise


def clean_law_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_life_protection_law_items() -> list[dict[str, object]]:
    xml_text = fetch_life_protection_xml()
    root = ET.fromstring(xml_text)
    items: list[dict[str, object]] = []
    for article in root.findall(".//MainProvision//Article"):
        title = clean_law_text("".join(article.findtext("ArticleTitle", default="")))
        if not title:
            continue
        caption = clean_law_text(article.findtext("ArticleCaption", default=""))
        body_lines: list[str] = []
        for paragraph in article.findall("./Paragraph"):
            paragraph_text = clean_law_text("".join(paragraph.itertext()))
            if paragraph_text and paragraph_text not in body_lines:
                body_lines.append(paragraph_text)
        body = "\n".join(body_lines).strip()
        if len(body) < 8:
            continue
        variants = article_number_variants(title)
        article_label = variants[-1] if variants else title
        display_title = f"生活保護法 {article_label}{caption}"
        refs = ["生活保護法", f"生活保護法{article_label}", f"法{article_label}"]
        refs.extend(f"法{variant}" for variant in variants if f"法{variant}" not in refs)
        items.append(
            {
                "id": f"生活保護法-{article_label}",
                "title": display_title,
                "chapter": "生活保護法",
                "sourceType": "law",
                "sourceUrl": f"https://laws.e-gov.go.jp/law/{LIFE_PROTECTION_LAW_ID}",
                "body": body,
                "references": refs[:8],
            }
        )
    return items


def main() -> None:
    text = extract_doc_text(SOURCE_DOC)
    TEXT_OUT.write_text(text, encoding="utf-8")
    case_items = build_items(text)
    law_items = build_life_protection_law_items()
    items = case_items + law_items
    payload = {
        "source": f"{SOURCE_DOC.name} / 生活保護法（e-Gov）",
        "sources": [SOURCE_DOC.name, "生活保護法（e-Gov法令検索）"],
        "itemCount": len(items),
        "caseItemCount": len(case_items),
        "lawItemCount": len(law_items),
        "items": items,
    }
    DATA_OUT.write_text(
        "window.APP_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {DATA_OUT} with {len(items)} items")


if __name__ == "__main__":
    main()
