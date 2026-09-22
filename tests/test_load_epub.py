from ebooklib import epub

from app.ingestion.load_epub import BookMetadata, clean_book_title, make_book_id
from app.ingestion.reader_epub import extract_reader_chapters


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
