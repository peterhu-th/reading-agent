from ebooklib import epub

from app.ingestion.chunking import chunk_paragraphs
from app.ingestion.load_epub import BookMetadata, clean_book_title, load_epub, make_book_id, reader_records_to_paragraphs
from app.ingestion.reader_epub import extract_reader_chapters, load_epub_for_reader
from app.models.schemas import ReaderContentRecord


def test_clean_book_title_removes_marketing_suffixes():
    assert clean_book_title("红楼梦（人文社权威定本彩皮版）") == "红楼梦"
    assert clean_book_title("心【上海译文出品】") == "心"
    assert clean_book_title("百年孤独(根据马尔克斯指定版本翻译)") == "百年孤独"
    assert clean_book_title("【精排】诗经") == "诗经"


def test_clean_book_title_keeps_real_subtitle():
    assert clean_book_title("疯癫与文明：理性时代的疯癫史") == "疯癫与文明：理性时代的疯癫史"


def test_book_id_disambiguates_reused_epub_identifier_and_survives_body_edits():
    shared_identifier = "urn:uuid:273fd756-62f2-4858-8d67-99e08f24bba9"
    autobiography = BookMetadata(title="从文自传", authors=("沈从文",), identifier=shared_identifier)
    lover = BookMetadata(title="情人", authors=("玛格丽特·杜拉斯",), identifier=shared_identifier)

    autobiography_id = make_book_id(autobiography, "first-file-version")

    assert autobiography_id != make_book_id(lover, "another-file")
    assert autobiography_id == make_book_id(autobiography, "edited-file-version")


def test_reader_uses_toc_and_preserves_short_poetry_lines():
    book = epub.EpubBook()
    document = epub.EpubHtml(uid="poems", file_name="poems.xhtml", title="内部标题")
    document.set_content(
        """<html><body>
        <h1 id="first">第一首</h1><p>风</p><p>落在河上</p>
        <h1 id="second">第二首</h1><p>月</p><p>照着故乡</p>
        </body></html>"""
    )
    book.add_item(document)
    book.spine = [("poems", "yes")]
    book.toc = (
        epub.Link("poems.xhtml#first", "诗一", "first"),
        epub.Link("poems.xhtml#second", "诗二", "second"),
    )

    chapters = extract_reader_chapters(book, book_type="poetry")

    assert [chapter.title for chapter in chapters] == ["诗一", "诗二"]
    assert [block.text for block in chapters[0].blocks] == ["第一首", "风", "落在河上"]
    assert chapters[0].blocks[1].kind == "verse"
    assert [block.text for block in chapters[1].blocks] == ["第二首", "月", "照着故乡"]


def test_reader_keeps_inline_footnote_markers_in_their_paragraph():
    book = epub.EpubBook()
    document = epub.EpubHtml(uid="chapter", file_name="chapter.xhtml", title="第一章")
    document.set_content(
        """<html><body>
        <h1 id="chapter">第一章<a href="#title-note"><sup>[1]</sup></a></h1>
        <p>正文前半<a href="#note"><sup>[1]</sup></a>正文后半<br/>下一行</p>
        <p id="note"><a href="#back">[1]</a> 脚注正文</p>
        </body></html>"""
    )
    book.add_item(document)
    book.spine = [("chapter", "yes")]
    book.toc = (epub.Link("chapter.xhtml#chapter", "第一章", "chapter-link"),)

    chapters = extract_reader_chapters(book)

    assert [block.text for block in chapters[0].blocks] == [
        "第一章[1]",
        "正文前半[1]正文后半",
        "下一行",
        "[1] 脚注正文",
    ]


def test_rag_keeps_reader_locations_after_toc_splits_and_filtering(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("shared-chapter-locations")
    book.set_title("章节映射测试")
    book.set_language("zh")
    copyright_page = epub.EpubHtml(uid="copyright", file_name="copyright.xhtml", title="版权")
    copyright_page.set_content("<html><body><h1>版权</h1><p>版权所有，本书内容未经授权不得复制或传播。</p></body></html>")
    document = epub.EpubHtml(uid="chapters", file_name="chapters.xhtml", title="内部文档名")
    first = "第一章的主人公独自走过长桥，随后沿着河流继续寻找过去的记忆。"
    second = "第二章的主人公终于抵达故乡，并且在那里遇到了多年未见的朋友。"
    document.set_content(f'<html><body><h1 id="first">第一章</h1><p>短行</p><p>{first}</p><h1 id="second">第二章</h1><p>{second}</p></body></html>')
    book.add_item(copyright_page)
    book.add_item(document)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [copyright_page, document]
    book.toc = (
        epub.Link("copyright.xhtml", "版权", "copyright-link"),
        epub.Link("chapters.xhtml#first", "第一章", "first"),
        epub.Link("chapters.xhtml#second", "第二章", "second"),
    )
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), book)

    reader_records = load_epub_for_reader(path)
    paragraphs = load_epub(path)
    assert [item.text for item in paragraphs] == [first, second]
    for paragraph in paragraphs:
        original = next(item for item in reader_records if item.text == paragraph.text)
        assert (paragraph.chapter_index, paragraph.paragraph_index) == (original.chapter_index, original.paragraph_index)
    chunks = chunk_paragraphs(paragraphs)
    reader_second = next(item for item in reader_records if item.text == second)
    matches = [chunk for chunk in chunks if chunk.chapter_index == reader_second.chapter_index]
    assert len(matches) == 1
    assert matches[0].text == second
    assert matches[0].start_paragraph_index == reader_second.paragraph_index


def test_rag_keeps_short_br_lines_when_source_paragraph_is_retrievable():
    lines = ["这是原文段落中的第一行文字。", "这是原文段落中的第二行文字。"]
    records = [
        ReaderContentRecord(
            book_id="book", title="测试书", chapter_index=3, paragraph_index=4 + index,
            text=line, source_href="chapter.xhtml", source_node_path="0/1/2", source_line_index=index,
        )
        for index, line in enumerate(lines)
    ]

    paragraphs = reader_records_to_paragraphs(records, "test.epub")

    assert [item.text for item in paragraphs] == lines
    assert [item.paragraph_index for item in paragraphs] == [4, 5]
    assert all(item.chapter_index == 3 for item in paragraphs)


def test_identical_sibling_paragraphs_have_distinct_source_locations():
    book = epub.EpubBook()
    document = epub.EpubHtml(uid="chapter", file_name="chapter.xhtml", title="章节")
    document.set_content("<html><body><p>相同正文</p><p>相同正文</p></body></html>")
    book.add_item(document)
    book.spine = [("chapter", "yes")]

    blocks = extract_reader_chapters(book)[0].blocks

    assert [block.text for block in blocks] == ["相同正文", "相同正文"]
    assert blocks[0].node_path != blocks[1].node_path
