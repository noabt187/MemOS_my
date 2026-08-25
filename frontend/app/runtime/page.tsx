"use client";

import { FormEvent, useState } from "react";
import Link from "@/lib/link";
import {
  Bot,
  CheckCircle2,
  DatabaseZap,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Search,
  Send,
  Upload,
} from "lucide-react";
import AppRail from "../components/AppRail";
import { appApi, IngestionResult, SearchResult } from "@/lib/api-client";

type RuntimeMode = "remember" | "chat" | "search";
type ChatTurn = { role: "user" | "assistant"; text: string };

export default function RuntimePage() {
  const [mode, setMode] = useState<RuntimeMode>("remember");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [sessionId] = useState(() => `web-${Date.now().toString(36)}`);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [rememberResult, setRememberResult] = useState<IngestionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const remember = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim() || loading) return;
    setLoading(true);
    setError("");
    setNotice("");
    setRememberResult(null);
    try {
      const result = await appApi.rememberText(text.trim());
      setRememberResult(result);
      setNotice("文字已经交给 MemOS 完成提取和写入。");
      setText("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "写入失败");
    } finally {
      setLoading(false);
    }
  };

  const chat = async (event: FormEvent) => {
    event.preventDefault();
    const nextQuery = query.trim();
    if (!nextQuery || loading) return;
    setQuery("");
    setError("");
    setNotice("");
    setChatTurns((turns) => [...turns, { role: "user", text: nextQuery }]);
    setLoading(true);
    try {
      const result = await appApi.chat(nextQuery, sessionId);
      setChatTurns((turns) => [...turns, { role: "assistant", text: result.response }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "对话失败");
    } finally {
      setLoading(false);
    }
  };

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    setError("");
    setNotice("");
    setSearchResult(null);
    try {
      setSearchResult(await appApi.search(query.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
  };

  const reconcile = async () => {
    if (reconciling) return;
    setReconciling(true);
    setError("");
    setNotice("");
    try {
      const result = await appApi.reconcileTopics();
      setNotice(
        result.removed_memories
          ? `对账完成，已从 Topic 证据中移除 ${result.removed_memories} 条失效记忆。`
          : "对账完成，Topic 证据与 MemOS 当前数据一致。",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Topic 对账失败");
    } finally {
      setReconciling(false);
    }
  };

  return (
    <main className="shell page-shell">
      <AppRail active="runtime" serviceHealthy={!error} />
      <section className="workspace inner-page runtime-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">MEMORY WORKBENCH</p>
            <h1>记忆交互</h1>
            <p className="subhead">在页面里完成文字写入、记忆问答和语义检索；解析、模型调用与 Topic 计算仍由后端负责。</p>
          </div>
          <Link className="secondary-action" href="/upload"><Upload size={15} />上传文件</Link>
        </header>

        <section className="runtime-identity panel" aria-label="运行范围">
          <div><span>当前操作范围</span><strong>用户和记忆库由应用后端统一配置</strong></div>
          <button onClick={() => void reconcile()} disabled={reconciling}>
            <RefreshCw className={reconciling ? "spin" : ""} size={15} />
            {reconciling ? "正在对账" : "Topic 对账"}
          </button>
        </section>

        <div className="runtime-tabs" role="tablist" aria-label="操作类型">
          <button className={mode === "remember" ? "active" : ""} onClick={() => setMode("remember")}><DatabaseZap size={16} />写入文字</button>
          <button className={mode === "chat" ? "active" : ""} onClick={() => setMode("chat")}><MessageSquareText size={16} />记忆问答</button>
          <button className={mode === "search" ? "active" : ""} onClick={() => setMode("search")}><Search size={16} />检索记忆</button>
        </div>

        {error && <div className="service-callout error"><strong>操作没有完成</strong><span>{error}</span></div>}
        {notice && <div className="service-callout success"><CheckCircle2 size={17} /><span>{notice}</span></div>}

        {mode === "remember" && (
          <section className="runtime-workspace panel">
            <div className="runtime-copy">
              <p className="section-kicker">TEXT INGEST</p><h2>直接写入一段文字</h2>
              <p>后端会使用 fine 模式提取记忆，补充结构化 info，并在成功后自动更新 Topic。</p>
            </div>
            <form onSubmit={remember}>
              <textarea value={text} onChange={(event) => setText(event.target.value)} rows={11} placeholder="例如：今天下午三点，我和林誉恒在图书馆讨论了期末项目，决定周五前完成演示稿。" />
              <button className="primary-action" disabled={!text.trim() || loading}>
                {loading ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}
                {loading ? "正在提取并写入" : "提取并写入记忆"}
              </button>
            </form>
            {rememberResult && (
              <div className="runtime-result-grid">
                <div><span>MemOS 新记忆</span><strong>{rememberResult.memories_created}</strong></div>
                <div><span>Topic 已处理记忆</span><strong>{rememberResult.topic.processed_memories}</strong></div>
                <div><span>当前滚动席位</span><strong>{rememberResult.topic.active_topics}</strong></div>
                {rememberResult.topic.error && <p>记忆已写入，但 Topic 更新失败：{rememberResult.topic.error}</p>}
              </div>
            )}
          </section>
        )}

        {mode === "chat" && (
          <section className="runtime-workspace chat-workspace panel">
            <div className="runtime-copy">
              <p className="section-kicker">READ-ONLY CHAT</p><h2>和记忆库对话</h2>
              <p>这里只检索并回答，不会把普通问答自动写回记忆库。需要保存的内容请使用“写入文字”或“上传文件”。</p>
            </div>
            <div className="chat-stream">
              {!chatTurns.length && <div className="runtime-empty"><Bot size={24} /><span>可以问：我最近在准备什么？我和某个人最近发生了什么？</span></div>}
              {chatTurns.map((turn, index) => <article className={turn.role} key={`${turn.role}-${index}`}><span>{turn.role === "user" ? "你" : "MemOS"}</span><p>{turn.text}</p></article>)}
              {loading && <article className="assistant pending"><span>MemOS</span><p><LoaderCircle className="spin" size={15} />正在检索记忆并组织回答</p></article>}
            </div>
            <form className="runtime-query-form" onSubmit={chat}>
              <textarea value={query} onChange={(event) => setQuery(event.target.value)} rows={3} placeholder="输入你想问记忆库的问题" />
              <button className="primary-action" disabled={!query.trim() || loading}><Send size={17} />发送</button>
            </form>
          </section>
        )}

        {mode === "search" && (
          <section className="runtime-workspace panel">
            <div className="runtime-copy">
              <p className="section-kicker">SEMANTIC SEARCH</p><h2>直接检索记忆</h2>
              <p>返回后端当前配置的 Top 5 结果和原始相关度；这个操作不会生成新记忆。</p>
            </div>
            <form className="runtime-query-form search-form" onSubmit={search}>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：期末考试、睡眠状况、和林誉恒吃饭" />
              <button className="primary-action" disabled={!query.trim() || loading}>{loading ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}检索</button>
            </form>
            {searchResult && (
              <div className="search-results">
                <div className="result-heading"><strong>检索结果</strong><span>{searchResult.total} 条</span></div>
                {searchResult.results.length ? searchResult.results.map((row, index) => (
                  <article key={row.id || String(index)}>
                    <b>{String(index + 1).padStart(2, "0")}</b>
                    <div><strong>{row.title}</strong><p>{row.content}</p></div>
                    <span>{typeof row.score === "number" ? row.score.toFixed(3) : ""}</span>
                  </article>
                )) : <div className="runtime-empty">没有检索到相关记忆。</div>}
              </div>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
