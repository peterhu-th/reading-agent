import { AlignJustify, Bot, Database, Library, Minus, Moon, Plus, Save, Search, Settings2, Sun, Undo2 } from "lucide-react";
import { useState } from "react";

import type { EditorState, ReaderPreferences } from "../types";

interface Props {
  title: string;
  chapterTitle: string;
  progress: number;
  preferences: ReaderPreferences;
  chatOpen: boolean;
  editorState: EditorState;
  saving: boolean;
  onLibrary: () => void;
  onChat: () => void;
  onPreferences: (value: ReaderPreferences) => void;
  onSearch: (query: string) => void;
  onUndo: () => void;
  onSave: () => void;
  onUpdateDatabase: () => void;
}

export function ReaderToolbar({ title, chapterTitle, progress, preferences, chatOpen, editorState, saving, onLibrary, onChat, onPreferences, onSearch, onUndo, onSave, onUpdateDatabase }: Props) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  return (
    <header className="reader-toolbar">
      <button className="icon-button" onClick={onLibrary} title="书库与目录"><Library /></button>
      <div className="reader-heading"><strong>{title || "选择一本书"}</strong><span>{chapterTitle}</span></div>
      <span className={`editor-status ${editorState.dirty ? "is-dirty" : ""}`}>{editorState.dirty ? "未保存" : "已保存"}</span>
      <span className="progress-label">{Math.round(progress)}%</span>
      <div className="toolbar-actions">
        <button aria-label="撤销" className="icon-button editor-action" disabled={!editorState.undo_count || editorState.database_updating} onClick={onUndo} title={editorState.last_operation ? `撤销：${editorState.last_operation}` : "撤销"}><Undo2 /><small>{editorState.undo_count || ""}</small></button>
        <button aria-label="保存" className="icon-button editor-action" disabled={!editorState.dirty || saving || editorState.database_updating} onClick={onSave} title="保存到 EPUB"><Save /></button>
        <button aria-label="更新数据库" className={`icon-button editor-action ${editorState.database_updating ? "is-updating" : ""}`} disabled={editorState.database_updating || saving} onClick={onUpdateDatabase} title="更新数据库"><Database /></button>
        <button className="icon-button" onClick={() => setSearchOpen((value) => !value)} title="搜索当前章节"><Search /></button>
        <button className="icon-button" onClick={() => setSettingsOpen((value) => !value)} title="阅读设置"><Settings2 /></button>
        <button className={`chat-toggle ${chatOpen ? "is-active" : ""}`} onClick={onChat}><Bot />{chatOpen ? "隐藏 AI" : "询问 AI"}</button>
      </div>
      {searchOpen && (
        <form className="toolbar-popover search-popover" onSubmit={(event) => { event.preventDefault(); onSearch(query); }}>
          <Search /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索当前章节" /><button type="submit">查找</button>
        </form>
      )}
      {settingsOpen && (
        <div className="toolbar-popover settings-popover">
          <div><span>字号</span><button title="减小字号" onClick={() => onPreferences({ ...preferences, fontSize: Math.max(14, preferences.fontSize - 1) })}><Minus /></button><b>{preferences.fontSize}</b><button title="增大字号" onClick={() => onPreferences({ ...preferences, fontSize: Math.min(26, preferences.fontSize + 1) })}><Plus /></button></div>
          <div><span>行距</span><input type="range" min="1.5" max="2.4" step="0.1" value={preferences.lineHeight} onChange={(event) => onPreferences({ ...preferences, lineHeight: Number(event.target.value) })} /></div>
          <div><span>版心</span>{(["narrow", "medium", "wide"] as const).map((width) => <button className={preferences.width === width ? "is-active" : ""} key={width} onClick={() => onPreferences({ ...preferences, width })}><AlignJustify />{width === "narrow" ? "窄" : width === "medium" ? "中" : "宽"}</button>)}</div>
          <div><span>主题</span>{(["light", "paper", "dark"] as const).map((theme) => <button className={preferences.theme === theme ? "is-active" : ""} key={theme} onClick={() => onPreferences({ ...preferences, theme })}>{theme === "dark" ? <Moon /> : <Sun />}{theme === "light" ? "明亮" : theme === "paper" ? "柔和" : "暗色"}</button>)}</div>
        </div>
      )}
    </header>
  );
}
