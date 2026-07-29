import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.answer_generator import generate_answer
from app.memory.conversation_memory import render_history, update_conversation
from app.models.schemas import ConversationSession, RetrievalResult
from app.retrieval.hybrid_retriever import EnhancedRetriever
from app.services.aiclient2api import ensure_aiclient2api_running


def print_retrieval_summary(result: RetrievalResult) -> None:
    count = len(result.chunks)
    if count == 0:
        print("未找到足够相关的书籍片段。")
        return

    titles = []
    for item in result.chunks:
        title = item.chunk.title
        if title not in titles:
            titles.append(title)
    title_text = "、".join(titles[:3])
    if len(titles) > 3:
        title_text += f" 等 {len(titles)} 本"
    print(
        f"已检索到 {count} 条相关证据，来源：{title_text}；"
        f"重排：{result.debug.reranker_backend}"
    )


def print_debug_results(result: RetrievalResult) -> None:
    print("\n检索调试信息：")
    for line in result.debug.lines:
        print(line)
    print()


def main() -> None:
    if not ensure_aiclient2api_running():
        raise SystemExit("AIClient2API is not healthy. Run: python scripts/start_api.py")

    print("Reading memory assistant. Type /exit to quit.")
    print("Commands: /debug on, /debug off, /history, /clear")
    retriever = EnhancedRetriever()
    conversation = ConversationSession()
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
        if question == "/history":
            print(render_history(conversation))
            continue
        if question == "/clear":
            conversation = ConversationSession()
            print("已清空本轮会话上下文。")
            continue

        retrieval_result = retriever.search(question, debug=debug, conversation=conversation)
        if debug:
            print_debug_results(retrieval_result)
        else:
            print_retrieval_summary(retrieval_result)

        try:
            result = generate_answer(
                question,
                retrieval_result.chunks,
                intent=retrieval_result.intent,
                conversation=conversation,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}")
            continue
        print(result.answer)
        if result.citations:
            print("\n引用来源：")
            for citation in result.citations:
                print(citation)
        conversation = update_conversation(conversation, question, retrieval_result, result)


if __name__ == "__main__":
    main()
