import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.citation_builder import format_citation
from app.memory.conversation_memory import render_history
from app.services.aiclient2api import ensure_aiclient2api_running
from app.services.reading_assistant import ReadingAssistantService


def main() -> None:
    if not ensure_aiclient2api_running():
        raise SystemExit("AIClient2API 未就绪，请先运行 python scripts/start_api.py。")

    service = ReadingAssistantService()
    session = service.create_session()
    debug = False
    print("阅读记忆助理已启动。输入 /exit 退出。")
    print("命令：/debug on、/debug off、/history、/clear")

    while True:
        question = input("> ").strip()
        if not question:
            continue
        if question == "/exit":
            break
        if question == "/debug on":
            debug = True
            print("调试信息已开启。")
            continue
        if question == "/debug off":
            debug = False
            print("调试信息已关闭。")
            continue
        if question == "/history":
            print(render_history(service.store.get(session.session_id)))
            continue
        if question == "/clear":
            service.store.delete(session.session_id)
            session = service.create_session()
            print("已清空本轮会话上下文。")
            continue

        try:
            prepared = service.prepare_turn(session.session_id, question, debug=debug)
            if prepared.resolved.clarification_needed:
                print(prepared.resolved.clarification_message)
                continue
            result = prepared.retrieval
            if result is None:
                print("未获得检索结果。")
                continue
            print(f"已找到 {len(result.chunks)} 条相关证据，完成 {len(result.rounds) - 1} 轮补充检索。")
            if debug:
                for line in result.debug.lines:
                    print(line)
            answer = service.generate(prepared)
            print(answer.answer)
            if answer.citations:
                print("\n引用来源：")
                for citation in answer.citations:
                    print(format_citation(citation))
            session = service.finalize_turn(prepared, answer)
        except RuntimeError as exc:
            print(f"错误：{exc}")


if __name__ == "__main__":
    main()
