import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.answer_generator import generate_answer
from app.retrieval.vector_retriever import VectorRetriever
from app.services.aiclient2api import ensure_aiclient2api_running


def print_debug_results(retrieved) -> None:
    for index, item in enumerate(retrieved, start=1):
        chunk = item.chunk
        print(
            f"[{index}] score={item.score} title={chunk.title} "
            f"chapter={chunk.chapter_title or chunk.chapter_index} "
            f"chunk_id={chunk.chunk_id}"
        )


def main() -> None:
    if not ensure_aiclient2api_running():
        raise SystemExit("AIClient2API is not healthy. Run: python scripts/start_api.py")

    print("Reading memory assistant. Type /exit to quit.")
    print("Use /debug on or /debug off to toggle retrieval details.")
    retriever = VectorRetriever()
    debug = False

    while True:
        question = input("> ").strip()
        if not question:
            continue
        if question == "/exit":
            break
        if question == "/debug on":
            debug = True
            print("Debug on")
            continue
        if question == "/debug off":
            debug = False
            print("Debug off")
            continue

        retrieved = retriever.search(question, top_k=5)
        if debug:
            print_debug_results(retrieved)

        try:
            result = generate_answer(question, retrieved)
        except RuntimeError as exc:
            print(f"Error: {exc}")
            continue
        print(result.answer)
        if result.citations:
            print("\nCitations:")
            for citation in result.citations:
                print(citation)


if __name__ == "__main__":
    main()
