from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from posixpath import dirname, join, normpath
from typing import Iterable
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from app.models.schemas import ReaderContentRecord
from app.services.reader import reader_edit_id


class EpubEditError(RuntimeError):
    pass


class EpubEditorService:
    """Write the current in-memory reader edits back to source EPUB files."""

    def __init__(self, raw_epub_dir: str | Path) -> None:
        self.raw_epub_dir = Path(raw_epub_dir).resolve()
        self._service_baselines: dict[Path, bytes] = {}

    def save(
        self,
        records: Iterable[ReaderContentRecord],
        edited_text: dict[str, str],
        deleted: set[str],
        chapter_titles: dict[tuple[str, int], str] | None = None,
    ) -> list[Path]:
        all_records = list(records)
        chapter_titles = chapter_titles or {}
        records_by_epub: dict[str, list[ReaderContentRecord]] = defaultdict(list)
        chapter_updates_by_epub: dict[str, list[tuple[set[str], str]]] = defaultdict(list)
        affected_ids = set(edited_text) | deleted
        for record in all_records:
            if reader_edit_id(record) in affected_ids and record.source_epub:
                records_by_epub[record.source_epub].append(record)
        for key, title in chapter_titles.items():
            chapter_records = [record for record in all_records if (record.book_id, record.chapter_index) == key and record.source_epub]
            if not chapter_records:
                continue
            source_epub = chapter_records[0].source_epub
            hrefs = {record.source_href for record in chapter_records if record.source_href}
            chapter_updates_by_epub[source_epub].append((hrefs, title))

        saved: list[Path] = []
        previously_saved = {path.name for path in self._service_baselines}
        for source_epub in set(records_by_epub) | set(chapter_updates_by_epub) | previously_saved:
            affected = records_by_epub.get(source_epub, [])
            chapter_updates = chapter_updates_by_epub.get(source_epub, [])
            source_path = (self.raw_epub_dir / source_epub).resolve()
            if source_path.parent != self.raw_epub_dir or not source_path.exists():
                raise EpubEditError(f"找不到原始 EPUB：{source_epub}")
            baseline = self._service_baselines.setdefault(source_path, source_path.read_bytes())
            temporary = source_path.with_name(f".{source_path.stem}.editing.epub")
            try:
                if affected or chapter_updates:
                    source_nodes = {(item.source_href, item.source_node_path) for item in affected}
                    node_records = [
                        item
                        for item in all_records
                        if item.source_epub == source_epub
                        and (item.source_href, item.source_node_path) in source_nodes
                    ]
                    self._write_modified_archive(baseline, temporary, node_records, edited_text, deleted, chapter_updates)
                else:
                    temporary.write_bytes(baseline)
                temporary.replace(source_path)
                saved.append(source_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return saved

    def _write_modified_archive(
        self,
        baseline: bytes,
        output_path: Path,
        records: list[ReaderContentRecord],
        edited_text: dict[str, str],
        deleted: set[str],
        chapter_updates: list[tuple[set[str], str]],
    ) -> None:
        by_document: dict[str, dict[str, list[ReaderContentRecord]]] = defaultdict(lambda: defaultdict(list))
        for record in records:
            if not record.source_node_path:
                raise EpubEditError("该段落缺少 EPUB 节点定位，请先重新生成阅读数据。")
            by_document[record.source_href][record.source_node_path].append(record)

        with ZipFile(BytesIO(baseline), "r") as source:
            archive_names = {normalize_href(info.filename): info.filename for info in source.infolist()}
            replacements: dict[str, bytes] = {}
            for source_href, nodes in by_document.items():
                normalized = normalize_href(source_href)
                archive_name = archive_names.get(normalized) or next(
                    (original for candidate, original in archive_names.items() if candidate.endswith("/" + normalized)),
                    None,
                )
                if archive_name is None:
                    raise EpubEditError(f"EPUB 内找不到正文文件：{source_href}")
                soup = BeautifulSoup(source.read(archive_name), "lxml")
                resolved_nodes: list[tuple[Tag, list[ReaderContentRecord]]] = []
                for path, node_lines in nodes.items():
                    node = resolve_tag_path(soup, path)
                    if node is None:
                        raise EpubEditError(f"EPUB 正文节点已经变化：{source_href}#{path}")
                    resolved_nodes.append((node, node_lines))

                # Resolve every source path before mutating the tree. Removing an
                # earlier sibling changes the numeric paths of all later nodes.
                for node, node_lines in resolved_nodes:
                    final_lines: list[str] = []
                    for record in sorted(node_lines, key=lambda value: value.source_line_index):
                        edit_id = reader_edit_id(record)
                        if edit_id not in deleted:
                            final_lines.append(edited_text.get(edit_id, record.text))
                    if not final_lines:
                        node.decompose()
                        continue
                    node.clear()
                    for index, line in enumerate(final_lines):
                        if index:
                            node.append(soup.new_tag("br"))
                        node.append(NavigableString(line))
                replacements[archive_name] = str(soup).encode("utf-8")

            self._update_navigation_titles(source, replacements, chapter_updates)

            with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as target:
                for info in source.infolist():
                    target.writestr(copy_zip_info(info), replacements.get(info.filename, source.read(info.filename)))

    def _update_navigation_titles(
        self,
        source: ZipFile,
        replacements: dict[str, bytes],
        updates: list[tuple[set[str], str]],
    ) -> None:
        if not updates:
            return
        for info in source.infolist():
            suffix = Path(info.filename).suffix.lower()
            if suffix not in {".xhtml", ".html", ".htm", ".ncx"}:
                continue
            data = replacements.get(info.filename, source.read(info.filename))
            soup = BeautifulSoup(data, "xml" if suffix == ".ncx" else "lxml")
            changed = False
            if suffix == ".ncx":
                for content in soup.find_all("content"):
                    source_ref = str(content.get("src", ""))
                    title = matching_title(info.filename, source_ref, updates)
                    if title is None:
                        continue
                    nav_point = content.find_parent(["navPoint", "navpoint"])
                    label = nav_point.find(["navLabel", "navlabel"]) if nav_point else None
                    text = label.find("text") if label else None
                    if text is not None:
                        text.string = title
                        changed = True
            else:
                for nav in soup.find_all("nav"):
                    for link in nav.find_all("a", href=True):
                        title = matching_title(info.filename, str(link.get("href", "")), updates)
                        if title is None:
                            continue
                        link.clear()
                        link.append(NavigableString(title))
                        changed = True
            if changed:
                replacements[info.filename] = str(soup).encode("utf-8")


def normalize_href(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def matching_title(container_name: str, reference: str, updates: list[tuple[set[str], str]]) -> str | None:
    path = unquote(urlsplit(reference).path)
    if not path:
        return None
    resolved = normalize_href(normpath(join(dirname(normalize_href(container_name)), path)))
    for hrefs, title in updates:
        for href in hrefs:
            normalized = normalize_href(href)
            if resolved == normalized or resolved.endswith("/" + normalized):
                return title
    return None


def resolve_tag_path(soup: BeautifulSoup, path: str) -> Tag | None:
    current: Tag | BeautifulSoup = soup
    try:
        for raw_index in path.split("/"):
            children = [child for child in current.children if isinstance(child, Tag)]
            current = children[int(raw_index)]
    except (IndexError, TypeError, ValueError):
        return None
    return current if isinstance(current, Tag) else None


def copy_zip_info(info: ZipInfo) -> ZipInfo:
    copied = ZipInfo(info.filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.extra = info.extra
    copied.create_system = info.create_system
    copied.external_attr = info.external_attr
    copied.internal_attr = info.internal_attr
    copied.flag_bits = info.flag_bits
    return copied
