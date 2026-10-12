import json
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.schemas import ReaderContentRecord, ReaderMutation, ReaderOperationRequest
from app.services.editor_session import EditorSessionService
from app.services.reader import ReaderService, reader_edit_id
from app.web.reader_routes import create_reader_tools_router


@pytest.fixture
def reading_api(tmp_path):
    records = [
        ReaderContentRecord(
            book_id=book_id, title="中文/书名", author="作者", chapter_index=chapter,
            chapter_title=f"章节 {chapter}", paragraph_index=position,
            text=f"关键词 Apple 段落 {position}", source_epub="D:/private/book.epub",
        )
        for book_id in ("a", "b") for chapter in (0, 1) for position in (0, 1)
    ]
    reader_path = tmp_path / "reader.jsonl"
    reader_path.write_text("\n".join(item.model_dump_json() for item in records), encoding="utf-8")
    reader = ReaderService(reader_path=reader_path)
    editor = EditorSessionService(reader)
    editor.annotation_path = tmp_path / "annotations.json"
    editor.bookmark_path = tmp_path / "bookmarks.json"
    editor.reset()
    app = FastAPI()
    app.include_router(create_reader_tools_router(reader, editor))
    with TestClient(app) as client:
        yield client, reader, editor


def annotation_payload(reader, book_id="a", chapter_index=0, paragraph_index=0):
    record = reader.chapters[(book_id, chapter_index)][paragraph_index]
    return {
        "chapter_index": chapter_index,
        "start_edit_id": reader_edit_id(record),
        "end_edit_id": reader_edit_id(record),
        "start_offset": 0,
        "end_offset": 3,
        "selected_text": "关键词",
        "comment": "一条笔记",
    }


def test_annotation_crud_is_scoped_and_patch_preserves_unspecified_fields(reading_api):
    client, reader, editor = reading_api
    response = client.post("/api/reader/books/a/annotations", json=annotation_payload(reader))
    assert response.status_code == 201
    created = response.json()["annotation"]
    annotation_id = created["annotation_id"]
    assert response.json()["state"]["dirty"]
    assert client.patch(f"/api/reader/books/b/annotations/{annotation_id}", json={"comment": "误改"}).status_code == 404
    assert client.delete(f"/api/reader/books/b/annotations/{annotation_id}").status_code == 404
    assert client.get(f"/api/reader/books/b/annotations/{annotation_id}/location").status_code == 404

    changed = client.patch(f"/api/reader/books/a/annotations/{annotation_id}", json={"comment": ""})
    assert changed.status_code == 200
    assert changed.json()["annotation"]["comment"] == ""
    assert changed.json()["annotation"]["color"] == "yellow"
    listing = client.get("/api/reader/books/a/annotations?limit=1").json()
    assert listing["total"] == 1
    assert listing["items"][0]["quote"] == "关键词"
    assert listing["items"][0]["reader_location"]["start_paragraph_index"] == 0
    location = client.get(f"/api/reader/books/a/annotations/{annotation_id}/location")
    assert location.json() == listing["items"][0]["reader_location"]

    deleted = client.delete(f"/api/reader/books/a/annotations/{annotation_id}")
    assert deleted.status_code == 200 and not deleted.json()["dirty"]
    assert client.get(f"/api/reader/books/a/annotations/{annotation_id}/location").status_code == 404
    editor.undo()
    assert client.get("/api/reader/books/a/annotations").json()["total"] == 1


@pytest.mark.parametrize("invalid", [{}, {"comment": None}, {"color": "purple"}, {"book_id": "b"}])
def test_annotation_patch_rejects_empty_null_and_unknown_fields(reading_api, invalid):
    client, reader, _ = reading_api
    annotation_id = client.post("/api/reader/books/a/annotations", json=annotation_payload(reader)).json()["annotation"]["annotation_id"]
    assert client.patch(f"/api/reader/books/a/annotations/{annotation_id}", json=invalid).status_code == 422


def test_annotation_invalid_range_does_not_mutate_editor(reading_api):
    client, reader, editor = reading_api
    payload = annotation_payload(reader)
    payload["end_offset"] = 1000
    assert client.post("/api/reader/books/a/annotations", json=payload).status_code == 422
    payload["end_offset"] = 3
    payload["chapter_index"] = 1
    assert client.post("/api/reader/books/a/annotations", json=payload).status_code == 422
    assert not editor.has_unsaved_changes


def test_bookmark_location_persistence_and_cross_book_isolation(reading_api):
    client, reader, editor = reading_api
    edit_id = reader_edit_id(reader.chapters[("a", 1)][1])
    response = client.post("/api/reader/books/a/bookmarks", json={"chapter_index": 1, "edit_id": edit_id, "label": "稍后重读"})
    assert response.status_code == 201
    bookmark_id = response.json()["bookmark"]["bookmark_id"]
    assert client.get("/api/reader/books/b/bookmarks").json()["items"] == []
    assert client.delete(f"/api/reader/books/b/bookmarks/{bookmark_id}").status_code == 404
    assert client.get(f"/api/reader/books/b/bookmarks/{bookmark_id}/location").status_code == 404
    assert client.get(f"/api/reader/books/a/bookmarks/{bookmark_id}/location").json() == {
        "book_id": "a", "chapter_index": 1, "start_paragraph_index": 1, "end_paragraph_index": 1,
    }
    editor.save()
    editor.reset()
    assert client.get("/api/reader/books/a/bookmarks").json()["items"][0]["label"] == "稍后重读"
    assert client.delete(f"/api/reader/books/a/bookmarks/{bookmark_id}").status_code == 200
    editor.undo()
    assert not editor.has_unsaved_changes


@pytest.mark.parametrize("query", ["q=", "q=%20%20", "q=a&limit=0", "q=a&limit=101", "q=a&offset=-1", "q=a&chapter_index=-1"])
def test_search_validation(reading_api, query):
    client, _, _ = reading_api
    assert client.get(f"/api/reader/books/a/search?{query}").status_code == 422


def test_search_paginates_full_book_and_accepts_chapter_zero(reading_api):
    client, _, _ = reading_api
    response = client.get("/api/reader/books/a/search", params={"q": "  APPLE  ", "limit": 3})
    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["offset"], page["limit"], page["next_offset"]) == (4, 0, 3, 3)
    assert [item["chapter_index"] for item in page["items"]] == [0, 0, 1]
    assert all(item["book_id"] == "a" and item["edit_id"] for item in page["items"])
    final = client.get("/api/reader/books/a/search?q=apple&limit=3&offset=3").json()
    assert len(final["items"]) == 1 and final["next_offset"] is None
    assert client.get("/api/reader/books/a/search?q=apple&chapter_index=0").json()["total"] == 2
    assert client.get("/api/reader/books/a/search?q=missing").json()["items"] == []
    assert client.get("/api/reader/books/a/search?q=apple&chapter_index=100").status_code == 404
    assert client.get("/api/reader/books/missing/search?q=apple").status_code == 404


def test_export_includes_all_current_notes_and_uses_safe_utf8_filename(reading_api):
    client, reader, editor = reading_api
    for chapter in (0, 1):
        client.post("/api/reader/books/a/annotations", json=annotation_payload(reader, chapter_index=chapter))
    client.post("/api/reader/books/b/annotations", json=annotation_payload(reader, book_id="b"))
    record = reader.chapters[("a", 0)][0]
    client.post("/api/reader/books/a/bookmarks", json={"chapter_index": 0, "edit_id": reader_edit_id(record)})
    editor.apply_operation(ReaderOperationRequest(book_id="a", mutations=[
        ReaderMutation(edit_id=reader_edit_id(record), text="新" + record.text),
    ]))
    assert client.get("/api/reader/books/a/annotations?limit=1").json()["next_offset"] == 1
    response = client.get("/api/reader/books/a/notes/export?format=json")
    assert response.status_code == 200
    exported = response.json()
    assert exported["has_unsaved_changes"]
    assert len(exported["annotations"]) == 2 and len(exported["bookmarks"]) == 1
    assert exported["bookmarks"][0]["quote"].startswith("新关键词")
    assert all(item["book_id"] == "a" for item in exported["annotations"])
    assert "source_epub" not in response.text and "D:/private" not in response.text
    assert "中文/书名-笔记.json" in unquote(response.headers["Content-Disposition"])
    assert "filename=\"reading-notes.json\"" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    markdown = client.get("/api/reader/books/a/notes/export?format=markdown")
    assert markdown.status_code == 200
    assert markdown.headers["Content-Type"].startswith("text/markdown")
    assert "中文/书名" in markdown.text and "一条笔记" in markdown.text and "新关键词" in markdown.text
    assert client.get("/api/reader/books/a/notes/export?format=pdf").status_code == 422


def test_search_location_does_not_fall_forward_to_another_paragraph_after_delete(reading_api):
    client, _, editor = reading_api
    hit = client.get("/api/reader/books/a/search?q=apple&limit=1").json()["items"][0]
    url = f"/api/reader/books/a/paragraphs/{hit['edit_id']}/location"
    assert client.get(url).json() == hit["reader_location"]
    editor.apply_operation(ReaderOperationRequest(book_id="a", mutations=[
        ReaderMutation(edit_id=hit["edit_id"], deleted=True),
    ]))
    assert client.get(url).status_code == 409
    assert client.get("/api/reader/books/a/search?q=apple").json()["total"] == 3


def test_stale_bookmark_is_visible_but_location_returns_conflict(reading_api):
    client, _, editor = reading_api
    editor.bookmark_path.write_text(json.dumps([{
        "bookmark_id": "stale", "book_id": "a", "chapter_index": 0,
        "edit_id": "removed-paragraph", "label": "旧位置",
    }]), encoding="utf-8")
    editor.reset()
    listing = client.get("/api/reader/books/a/bookmarks").json()
    assert listing["items"][0]["reader_location"] is None
    assert client.get("/api/reader/books/a/bookmarks/stale/location").status_code == 409


def test_openapi_describes_pagination_mutations_and_export(reading_api):
    client, _, _ = reading_api
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    create = paths["/api/reader/books/{book_id}/bookmarks"]["post"]
    assert "201" in create["responses"] and "422" in create["responses"]
    search = paths["/api/reader/books/{book_id}/search"]["get"]
    page_schema_name = search["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert {"items", "total", "offset", "limit", "next_offset"} <= set(schema["components"]["schemas"][page_schema_name]["properties"])
    exported = paths["/api/reader/books/{book_id}/notes/export"]["get"]["responses"]["200"]["content"]
    assert set(exported) == {"application/json", "text/markdown"}
    assert "NotesExport" in schema["components"]["schemas"]
