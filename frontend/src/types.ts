export interface ReaderLocation {
  book_id: string;
  chapter_index: number;
  start_paragraph_index: number;
  end_paragraph_index: number;
}

export interface Citation {
  source_id: string;
  display_index: number;
  title: string;
  author: string;
  chapter_title: string;
  paragraph_range: string;
  excerpt: string;
  reader_location?: ReaderLocation | null;
}

export interface ConversationTurn {
  user_question: string;
  assistant_answer: string;
  citations: Citation[];
}

export interface Session {
  session_id: string;
  title: string;
  turns: ConversationTurn[];
  explicit_book_titles: string[];
  updated_at: string;
}

export interface ReaderBook {
  book_id: string;
  title: string;
  author: string;
  book_type: string;
  chapter_count: number;
  paragraph_count: number;
}

export interface ReaderChapter {
  book_id: string;
  chapter_index: number;
  chapter_title: string;
  depth: number;
  paragraph_count: number;
  preview: string;
}

export interface ReaderParagraph {
  paragraph_index: number;
  edit_id: string;
  text: string;
  kind: "paragraph" | "heading" | "verse" | "quote" | "list";
  annotations: ReaderAnnotation[];
}

export type AnnotationColor = "yellow" | "green" | "blue" | "red";

export interface ReaderAnnotation {
  annotation_id: string;
  book_id: string;
  chapter_index: number;
  start_edit_id: string;
  end_edit_id: string;
  start_offset: number;
  end_offset: number;
  selected_text: string;
  color: AnnotationColor;
  comment: string;
  created_at: string;
  updated_at: string;
}

export interface ReaderMutation {
  edit_id: string;
  text: string;
  deleted: boolean;
}

export interface EditorState {
  dirty: boolean;
  undo_count: number;
  last_operation: string;
  database_updating: boolean;
  database_status: string;
  database_message: string;
}

export interface DatabaseUpdateState {
  running: boolean;
  status: string;
  message: string;
  return_code: number | null;
}

export interface ReaderPage {
  book: ReaderBook;
  chapter: ReaderChapter;
  paragraphs: ReaderParagraph[];
  offset: number;
  next_offset: number | null;
  previous_chapter_index: number | null;
  next_chapter_index: number | null;
}

export interface ReaderSearchHit {
  chapter_index: number;
  chapter_title: string;
  paragraph_index: number;
  excerpt: string;
}

export interface RetrievalTrace {
  rounds: Array<{ round_index: number; new_chunk_count: number; stop_reason: string }>;
  evidence_sufficient: boolean;
  missing_aspects: string[];
  debug_lines: string[];
}

export interface HealthStatus {
  status: string;
  web_api_version?: number;
  editor_capabilities?: string[];
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

export type ChatScope = "library" | "book" | "chapter";
export type ReaderTheme = "light" | "paper" | "dark";
export type ReaderWidth = "narrow" | "medium" | "wide";

export interface ReaderPreferences {
  fontSize: number;
  lineHeight: number;
  width: ReaderWidth;
  theme: ReaderTheme;
}
