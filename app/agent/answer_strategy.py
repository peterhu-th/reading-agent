from app.models.schemas import AnswerStrategy, IntentAnalysis


def choose_answer_strategy(intent: IntentAnalysis) -> AnswerStrategy:
    labels = set(intent.labels)
    need_types = set(intent.need_types)
    if "comparison" in labels or "compare" in need_types:
        return AnswerStrategy(name="compare", min_chars=900, max_chars=1400, instruction="分别分析每本书，再比较共同点、差异和主题含义，每个关键判断都要有证据。")
    if "summary" in labels or "summarize" in need_types:
        return AnswerStrategy(name="summarize", min_chars=800, max_chars=1200, instruction="覆盖主要发展阶段、核心人物或观点、关键转折与主题，先整体后细节。")
    if "recommendation" in labels or "emotion" in labels:
        return AnswerStrategy(name="recommend", min_chars=600, max_chars=1000, instruction="先回应用户处境，再说明推荐片段及其相关原因，避免把文学阅读替代专业建议。")
    if "detail" in labels or "cite_detail" in need_types:
        return AnswerStrategy(name="detail", min_chars=300, max_chars=600, instruction="围绕具体细节，按照事件、人物关系或论证推进的顺序组织证据。")
    return AnswerStrategy(name="explain", min_chars=400, max_chars=800, instruction="直接回答问题，并把判断建立在多个证据片段上。")
