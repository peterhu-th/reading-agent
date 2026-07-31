export interface Citation {
  source_id: string;
  display_index: number;
  title: string;
  author: string;
  chapter_title: string;
  paragraph_range: string;
  excerpt: string;
}

export interface ConversationTurn {
  user_question: string;
  resolved_question: string;
  assistant_answer: string;
  answer_summary: string;
  citations: Citation[];
  book_titles: string[];
  authors: string[];
  entities: string[];
  topics: string[];
  missing_aspects: string[];
}

export interface Session {
  session_id: string;
  title: string;
  turns: ConversationTurn[];
  active_book_titles: string[];
  active_authors: string[];
  active_entities: string[];
  active_topics: string[];
  explicit_book_titles: string[];
  rolling_summary: string;
  updated_at: string;
}

export interface Book {
  book_id: string;
  title: string;
  author: string;
  book_type: string;
  chapter_count: number;
  chunk_count: number;
}

export interface SourceDetail {
  source_id: string;
  title: string;
  author: string;
  book_type: string;
  chapter_index: number;
  chapter_title: string;
  paragraph_range: string;
  text: string;
}

export interface RetrievalTrace {
  rounds: Array<{
    round_index: number;
    new_chunk_count: number;
    stop_reason: string;
    assessment?: { sufficient: boolean; missing_aspects: string[] };
  }>;
  evidence_sufficient: boolean;
  missing_aspects: string[];
  debug_lines: string[];
}

export interface HealthStatus {
  status: string;
  answer_api_healthy: boolean;
  answer_provider_ready: boolean;
  chunk_index_exists: boolean;
  chunks_exist: boolean;
  embedding_model_exists: boolean;
  reranker_model_exists: boolean;
}

export interface UiMessage {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  pending?: boolean;
}
