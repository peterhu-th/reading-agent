from app.models.schemas import AnswerStrategy, IntentAnalysis


def choose_answer_strategy(intent: IntentAnalysis) -> AnswerStrategy:
    labels = set(intent.labels)
    need_types = set(intent.need_types)

    if "comparison" in labels or "compare" in need_types:
        return AnswerStrategy(
            name="compare",
            min_chars=900,
            max_chars=1400,
            instruction="分别列出每本书的证据，再比较共同点、差异和背后的主题含义。",
        )
    if "summary" in labels or "summarize" in need_types:
        return AnswerStrategy(
            name="summarize",
            min_chars=800,
            max_chars=1200,
            instruction="覆盖情节脉络、核心人物、主题变化和关键证据，先整体后细节。",
        )
    if "recommendation" in labels or "emotion" in labels:
        return AnswerStrategy(
            name="recommend",
            min_chars=600,
            max_chars=1000,
            instruction="先回应用户状态，再解释哪些片段能对应这种处境，以及为什么相关。",
        )
    if "detail" in labels or "cite_detail" in need_types:
        return AnswerStrategy(
            name="detail",
            min_chars=300,
            max_chars=600,
            instruction="围绕细节问题回答，按事件、人物关系或论证推进的顺序组织证据。",
        )
    return AnswerStrategy(
        name="explain",
        min_chars=400,
        max_chars=800,
        instruction="直接回答问题，并把判断建立在多个证据片段上。",
    )
