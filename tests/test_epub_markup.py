import warnings
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

from app.ingestion import reader_epub
from app.ingestion.epub_source import parse_epub_html, read_epub_file
from app.ingestion.load_epub import html_to_text
from app.ingestion.reader_epub import extract_reader_blocks, flatten_toc, load_epub_for_reader, tag_path
from app.services.epub_editor import EpubEditorService, resolve_tag_path
from app.services.reader import reader_edit_id


@pytest.fixture(autouse=True)
def xml_as_html_warning_is_an_error():
    with warnings.catch_warnings():
        warnings.simplefilter("error", XMLParsedAsHTMLWarning)
        yield


def old_html_parser(markup):
    # Capture the warning only for the old parsing path used for compatibility.
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always", XMLParsedAsHTMLWarning)
        return BeautifulSoup(markup, "lxml")


@pytest.mark.parametrize("markup", [
    '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>中文标题</title></head><body><p>甲&nbsp;乙<em>中文</em>&copy;<br/>下一行&#x3002;</p></body></html>',
    '<html><body><p>未闭合中文段落<div>修复容器</div><p>后续&nbsp;内容<br>第二行</body></html>',
])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_html_and_xhtml_keep_existing_element_paths_and_text(markup, as_bytes):
    source = markup.encode("utf-8") if as_bytes else markup
    old = old_html_parser(source)
    parsed = parse_epub_html(source)

    assert [(node.name, tag_path(node), node.get_text()) for node in parsed.find_all(True)] == [
        (node.name, tag_path(node), node.get_text()) for node in old.find_all(True)
    ]
    assert [block.text for block in extract_reader_blocks(parsed)] == [block.text for block in extract_reader_blocks(old)]
    for block in extract_reader_blocks(old):
        located = resolve_tag_path(parsed, block.node_path)
        assert located is not None
        assert located.get_text() == block.node.get_text()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "gb18030"])
def test_xml_declared_encoding_is_kept_when_removing_the_declaration(encoding):
    source = f'<?xml version="1.0" encoding="{encoding}"?><html><body><p>中文段落 &amp; 引用 &copy;</p></body></html>'.encode(encoding)
    parsed = parse_epub_html(source)

    assert parsed.p.get_text() == "中文段落 & 引用 ©"
    assert parsed.p.get_text() == old_html_parser(source).p.get_text()


def test_legacy_text_extraction_uses_the_same_xhtml_parser():
    text, title = html_to_text('<?xml version="1.0"?><html><body><h1>中文标题</h1><p>甲&nbsp;乙<br/>下一行</p></body></html>')

    assert title == "中文标题"
    assert text == "中文标题\n甲 乙 下一行"


def test_existing_reader_locations_survive_xhtml_edit_save_and_toc_updates(tmp_path, monkeypatch):
    book = epub.EpubBook()
    book.set_identifier("xhtml-parser-compatibility")
    book.set_title("中文解析测试")
    book.set_language("zh")
    first = epub.EpubHtml(uid="first", file_name="first.xhtml", title="第一章", lang="zh")
    first.content = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="start">第一章</h1><p>中文&nbsp;正文<br/>第二行</p></body></html>'.encode("utf-8")
    second = epub.EpubHtml(uid="second", file_name="second.xhtml", title="第二章", lang="zh")
    second.content = '<html><body><h1 id="next">第二章</h1><p>保留 &copy; 尾段</p></body></html>'
    for item in (first, second, epub.EpubNav(), epub.EpubNcx()):
        book.add_item(item)
    book.spine = [first, second]
    book.toc = (epub.Link("first.xhtml#start", "第一章", "first"), epub.Link("second.xhtml#next", "第二章", "second"))
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), book)

    with monkeypatch.context() as previous_parser:
        previous_parser.setattr(reader_epub, "parse_epub_html", old_html_parser)
        old_records = load_epub_for_reader(path)
    assert load_epub_for_reader(path) == old_records
    target = next(item for item in old_records if item.text == "第二行")
    assert target.source_line_index == 1
    EpubEditorService(tmp_path).save(
        old_records,
        {reader_edit_id(target): "修改后的中文 & 引用"},
        set(),
        {(target.book_id, target.chapter_index): "新的章节名称"},
    )

    updated = load_epub_for_reader(path)
    assert [item.text for item in updated] == ["第一章", "中文 正文", "修改后的中文 & 引用", "第二章", "保留 © 尾段"]
    assert [entry.title for entry in flatten_toc(read_epub_file(path).toc)] == ["新的章节名称", "第二章"]
    with ZipFile(path) as archive:
        nav = parse_epub_html(archive.read("EPUB/nav.xhtml"))
        ncx = BeautifulSoup(archive.read("EPUB/toc.ncx"), "xml")
    assert nav.find("a", href="first.xhtml#start").get_text() == "新的章节名称"
    assert ncx.find("navLabel").get_text(strip=True) == "新的章节名称"
