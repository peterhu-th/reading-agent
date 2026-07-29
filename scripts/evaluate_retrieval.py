import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RERANK_BACKEND", "lightweight")

import app.retrieval.hybrid_retriever as hybrid_retriever
from app.agent.intent_analyzer import fallback_intent
from app.retrieval.hybrid_retriever import EnhancedRetriever


DEFAULT_QUESTIONS = [
    {"question": "《荒原狼》主要讲了什么？", "expected_title": "荒原狼"},
    {"question": "红楼梦里宝玉和黛玉的关系是怎么发展的？", "expected_title": "红楼梦"},
    {"question": "比较《瓦尔登湖》和《荒原狼》对孤独的理解。", "expected_title": "荒原狼"},
    {"question": "我最近很焦虑，有没有书里的片段能回应这种状态？", "expected_title": ""},
    {"question": "只根据《百年孤独》回答，布恩迪亚家族的循环体现在哪里？", "expected_title": "百年孤独"},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default="", help="Optional JSONL question file.")
    parser.add_argument("--llm-intent", action="store_true", help="Use online LLM intent analysis.")
    args = parser.parse_args()

    if not args.llm_intent:
        hybrid_retriever.analyze_intent = fallback_intent

    questions = load_questions(args.questions) if args.questions else DEFAULT_QUESTIONS
    retriever = EnhancedRetriever()
    passed = 0
    for item in questions:
        result = retriever.search(item["question"], debug=False)
        titles = [chunk.chunk.title for chunk in result.chunks]
        expected = item.get("expected_title", "")
        title_hit = not expected or any(expected in title for title in titles)
        enough_evidence = len(result.chunks) >= 5
        ok = title_hit and enough_evidence
        passed += int(ok)
        status = "ok" if ok else "fail"
        print(
            f"[{status}] {item['question']} | evidence={len(result.chunks)} | "
            f"titles={unique_titles(titles)[:3]}"
        )
    print(f"Passed {passed}/{len(questions)} retrieval checks.")
    if passed != len(questions):
        raise SystemExit(1)


def load_questions(path: str) -> list[dict[str, str]]:
    input_path = Path(path)
    questions = []
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def unique_titles(titles: list[str]) -> list[str]:
    result = []
    for title in titles:
        if title not in result:
            result.append(title)
    return result


if __name__ == "__main__":
    main()
