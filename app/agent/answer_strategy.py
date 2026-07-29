from app.models.schemas import AnswerStrategy, IntentAnalysis


def choose_answer_strategy(intent: IntentAnalysis) -> AnswerStrategy:
    labels = set(intent.labels)
    need_types = set(intent.need_types)

    if "comparison" in labels or "compare" in need_types:
        return AnswerStrategy(
            name="compare",
            min_chars=900,
            max_chars=1400,
            instruction="用对照结构回答，先分别概括每本书的证据，再总结共同点和差异。",
        )
    if "summary" in labels or "summarize" in need_types:
        return AnswerStrategy(
            name="summarize",
            min_chars=800,
            max_chars=1200,
            instruction="回答主要内容时要覆盖情节脉络、核心人物、主题变化和关键证据。",
        )
    if "recommendation" in labels or "emotion" in labels:
        return AnswerStrategy(
            name="recommend",
            min_chars=600,
            max_chars=1000,
            instruction="先回应用户状态，再结合书中片段解释为什么这些证据相关。",
        )
    if "detail" in labels or "cite_detail" in need_types:
        return AnswerStrategy(
            name="detail",
            min_chars=300,
            max_chars=600,
            instruction="围绕细节问题回答，按事件或关系的发展顺序组织证据。",
        )
    return AnswerStrategy(
        name="explain",
        min_chars=400,
        max_chars=800,
        instruction="直接回答问题，并把判断建立在多个证据片段上。",
    )
