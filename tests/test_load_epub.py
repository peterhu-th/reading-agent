from ebooklib import epub

from app.ingestion.load_epub import clean_book_title
from app.ingestion.reader_epub import extract_reader_chapters


def test_clean_book_title_removes_marketing_suffixes():
    assert clean_book_title("红楼梦（人文社权威定本彩皮版）") == "红楼梦"
    assert clean_book_title("心【上海译文出品】") == "心"
    assert clean_book_title("百年孤独(根据马尔克斯指定版本翻译)") == "百年孤独"
    assert clean_book_title("【精排】诗经") == "诗经"


def test_clean_book_title_keeps_real_subtitle():
    assert clean_book_title("疯癫与文明：理性时代的疯癫史") == "疯癫与文明：理性时代的疯癫史"


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
