from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from posixpath import dirname, join, normpath
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import Iterable, Iterator
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from app.ingestion.epub_source import parse_epub_html
from app.ingestion.reader_epub import load_epub_for_reader
from app.models.schemas import ReaderContentRecord
from app.services.reader import reader_edit_id


class EpubEditError(RuntimeError):
    pass


@dataclass
class PreparedEpubSave:
    files: dict[Path, Path]
    records: list[ReaderContentRecord]
    records_by_previous_id: dict[str, ReaderContentRecord]


class EpubEditorService:
    """Write the current in-memory reader edits back to source EPUB files."""

    def __init__(self, raw_epub_dir: str | Path) -> None:
        self.raw_epub_dir = Path(raw_epub_dir).resolve()

    def save(
        self,
        records: Iterable[ReaderContentRecord],
        edited_text: dict[str, str],
        deleted: set[str],
        chapter_titles: dict[tuple[str, int], str] | None = None,
    ) -> list[Path]:
        with self.prepare(records, edited_text, deleted, chapter_titles) as prepared:
            replace_saved_files(prepared.files)
            return list(prepared.files)

    @contextmanager
    def prepare(
        self,
        records: Iterable[ReaderContentRecord],
        edited_text: dict[str, str],
        deleted: set[str],
        chapter_titles: dict[tuple[str, int], str] | None = None,
    ) -> Iterator[PreparedEpubSave]:
        """Stage EPUBs and derive reader locations from the exact staged files."""

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

        records_by_previous_id: dict[str, ReaderContentRecord] = {}
        for record in all_records:
            edit_id = reader_edit_id(record)
            if edit_id in deleted:
                continue
            updates: dict[str, str] = {}
            if edit_id in edited_text and edited_text[edit_id] != record.text:
                updates["text"] = edited_text[edit_id]
            title = chapter_titles.get((record.book_id, record.chapter_index))
            if title is not None and title != record.chapter_title:
                updates["chapter_title"] = title
            records_by_previous_id[edit_id] = record.model_copy(update=updates) if updates else record
        changed_sources = set(records_by_epub) | set(chapter_updates_by_epub)
        if not changed_sources:
            yield PreparedEpubSave({}, list(records_by_previous_id.values()), records_by_previous_id)
            return

        with TemporaryDirectory(prefix=".reader-save-", dir=self.raw_epub_dir) as directory:
            files: dict[Path, Path] = {}
            rebuilt: dict[str, list[ReaderContentRecord]] = {}
            for source_epub in sorted(changed_sources):
                source_path = (self.raw_epub_dir / source_epub).resolve()
                if source_path.parent != self.raw_epub_dir or not source_path.exists():
                    raise EpubEditError(f"找不到原始 EPUB：{source_epub}")
                source_records = [item for item in all_records if item.source_epub == source_epub]
                affected_hrefs = {item.source_href for item in records_by_epub.get(source_epub, [])}
                document_records = [item for item in source_records if item.source_href in affected_hrefs]
                temporary = Path(directory) / source_path.name
                locations = self._write_modified_archive(
                    source_path.read_bytes(), temporary, document_records, edited_text, deleted,
                    chapter_updates_by_epub.get(source_epub, []),
                )
                fresh_records = load_epub_for_reader(temporary)
                fresh_by_location = {
                    (item.source_href, item.source_node_path, item.source_line_index): item
                    for item in fresh_records
                }
                legacy_matches: dict[str, ReaderContentRecord] = {}
                for href in {item.source_href for item in source_records if not item.source_node_path and item.source_href not in affected_hrefs}:
                    previous = [item for item in source_records if item.source_href == href]
                    current = [item for item in fresh_records if item.source_href == href]
                    legacy_matches.update((reader_edit_id(old), new) for old, new in zip(previous, current, strict=True))
                for record in source_records:
                    previous_id = reader_edit_id(record)
                    if previous_id in deleted:
                        continue
                    location = locations.get(previous_id, (record.source_href, record.source_node_path, record.source_line_index))
                    fresh = legacy_matches.get(previous_id) or fresh_by_location.get(location)
                    if fresh is None:
                        raise EpubEditError(f"保存后无法定位正文：{record.source_href}#{record.source_node_path}")
                    records_by_previous_id[previous_id] = fresh
                rebuilt[source_epub] = fresh_records
                files[source_path] = temporary

            saved_records: list[ReaderContentRecord] = []
            included_sources: set[str] = set()
            for record in all_records:
                if record.source_epub in rebuilt:
                    if record.source_epub not in included_sources:
                        saved_records.extend(rebuilt[record.source_epub])
                        included_sources.add(record.source_epub)
                elif reader_edit_id(record) not in deleted:
                    saved_records.append(records_by_previous_id[reader_edit_id(record)])
            yield PreparedEpubSave(files, saved_records, records_by_previous_id)

    def _write_modified_archive(
        self,
        baseline: bytes,
        output_path: Path,
        records: list[ReaderContentRecord],
        edited_text: dict[str, str],
        deleted: set[str],
        chapter_updates: list[tuple[set[str], str]],
    ) -> dict[str, tuple[str, str, int]]:
        by_document: dict[str, dict[str, list[ReaderContentRecord]]] = defaultdict(lambda: defaultdict(list))
        for record in records:
            by_document[record.source_href][record.source_node_path].append(record)
        locations: dict[str, tuple[str, str, int]] = {}

        with ZipFile(BytesIO(baseline), "r") as source:
            archive_names = {normalize_href(info.filename): info.filename for info in source.infolist()}
            replacements: dict[str, bytes] = {}
            for source_href, nodes in by_document.items():
                legacy_document_records = nodes.pop("", [])
                normalized = normalize_href(source_href)
                archive_name = archive_names.get(normalized) or next(
                    (original for candidate, original in archive_names.items() if candidate.endswith("/" + normalized)),
                    None,
                )
                if archive_name is None:
                    raise EpubEditError(f"EPUB 内找不到正文文件：{source_href}")
                soup = parse_epub_html(source.read(archive_name))
                resolved_nodes: list[tuple[Tag, list[ReaderContentRecord]]] = []
                for path, node_lines in nodes.items():
                    node = resolve_tag_path(soup, path)
                    if node is None:
                        raise EpubEditError(f"EPUB 正文节点已经变化：{source_href}#{path}")
                    resolved_nodes.append((node, node_lines))

                apply_legacy_document_records(soup, legacy_document_records, deleted)

                # Resolve every source path before mutating the tree. Removing an
                # earlier sibling changes the numeric paths of all later nodes.
                for node, node_lines in resolved_nodes:
                    if not any(reader_edit_id(record) in edited_text or reader_edit_id(record) in deleted for record in node_lines):
                        continue
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
                for node, node_lines in resolved_nodes:
                    surviving = [record for record in sorted(node_lines, key=lambda value: value.source_line_index) if reader_edit_id(record) not in deleted]
                    if not surviving:
                        continue
                    if node.parent is None:
                        raise EpubEditError(f"编辑删除了其他正文节点：{source_href}")
                    path = reader_node_path(node)
                    for line_index, record in enumerate(surviving):
                        locations[reader_edit_id(record)] = (source_href, path, line_index)
                replacements[archive_name] = str(soup).encode("utf-8")

            self._update_navigation_titles(source, replacements, chapter_updates)

            with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as target:
                for info in source.infolist():
                    target.writestr(copy_zip_info(info), replacements.get(info.filename, source.read(info.filename)))
        return locations

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
            soup = BeautifulSoup(data, "xml") if suffix == ".ncx" else parse_epub_html(data)
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


def apply_legacy_document_records(
    soup: BeautifulSoup,
    records: list[ReaderContentRecord],
    deleted: set[str],
) -> None:
    """Support whole-page deletion for reader data created before fallback paths existed."""

    if not records:
        return
    if any(reader_edit_id(record) not in deleted for record in records):
        raise EpubEditError("该段落使用旧版 EPUB 节点定位，请重新生成阅读数据后再编辑单个段落。")
    body = soup.body
    if body is None:
        raise EpubEditError("EPUB 正文页面缺少 body 节点，无法删除该章节。")
    body.clear()


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
            children = reader_children(current)
            current = children[int(raw_index)]
    except (IndexError, TypeError, ValueError):
        return None
    return current if isinstance(current, Tag) else None


def reader_children(node: Tag | BeautifulSoup) -> list[Tag]:
    # The reader removes these nodes before computing numeric element paths.
    return [child for child in node.children if isinstance(child, Tag) and child.name not in {"script", "style", "noscript"}]


def reader_node_path(node: Tag) -> str:
    parts: list[str] = []
    current = node
    while current.parent is not None:
        siblings = reader_children(current.parent)
        parts.append(str(next(index for index, sibling in enumerate(siblings) if sibling is current)))
        current = current.parent
    return "/".join(reversed(parts))


def replace_saved_files(files: dict[Path, Path]) -> None:
    """Replace staged outputs, restoring completed replacements on I/O failure."""

    backups: dict[Path, Path | None] = {}
    replaced: list[Path] = []
    try:
        for destination, staged in files.items():
            backup = staged.with_name(staged.name + ".previous") if destination.exists() else None
            if backup is not None:
                copyfile(destination, backup)
            backups[destination] = backup
        for destination, staged in files.items():
            staged.replace(destination)
            replaced.append(destination)
    except OSError:
        for destination in reversed(replaced):
            backup = backups[destination]
            if backup is None:
                destination.unlink()
            else:
                backup.replace(destination)
        raise
    finally:
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


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
