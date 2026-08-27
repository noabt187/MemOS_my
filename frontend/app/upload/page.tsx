"use client";

import { CheckCircle2, FileText, Image, Link2, LoaderCircle, UploadCloud, Video } from "lucide-react";
import { type DragEvent, type FormEvent, type ReactElement, useRef, useState } from "react";

import { appApi } from "@/lib/api-client.ts";
import type { IngestionResult } from "@/lib/api-client.ts";
import AppRail from "../components/AppRail.tsx";

type SelectedFileIconProps = {
  file: File | null;
};

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function SelectedFileIcon({ file }: SelectedFileIconProps): ReactElement {
  if (!file) return <UploadCloud size={36} />;
  if (file.type.startsWith("video/")) return <Video size={32} />;
  if (file.type.startsWith("image/")) return <Image size={32} />;
  return <FileText size={32} />;
}

export default function UploadPage(): ReactElement {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [instruction, setInstruction] = useState("");
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IngestionResult | null>(null);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [remoteSubmitting, setRemoteSubmitting] = useState(false);
  const [remoteResult, setRemoteResult] = useState<IngestionResult | null>(null);

  function chooseFile(nextFile?: File): void {
    if (!nextFile) return;
    setFile(nextFile);
    setResult(null);
    setError("");
  }

  function onDragOver(event: DragEvent<HTMLButtonElement>): void {
    event.preventDefault();
    setDragging(true);
  }

  function onDrop(event: DragEvent<HTMLButtonElement>): void {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setError("");
    setResult(null);
    try {
      setResult(await appApi.ingestFile(file, instruction));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接上传服务");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitRemote(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!remoteUrl.trim() || remoteSubmitting) return;
    setRemoteSubmitting(true);
    setError("");
    setRemoteResult(null);
    try {
      setRemoteResult(await appApi.ingestVideo(remoteUrl.trim(), instruction.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接上传服务");
    } finally {
      setRemoteSubmitting(false);
    }
  }

  return (
    <main className="shell page-shell">
      <AppRail active="upload" />
      <section className="workspace inner-page upload-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">UNIFIED MEMORY INGEST</p>
            <h1>上传到记忆</h1>
            <p className="subhead">只选择文件。系统自动识别文字、图片、图文或视频，并复用现有 Runtime。</p>
          </div>
        </header>

        <div className="upload-layout">
          <div className="upload-stack">
          <form className="upload-card panel" onSubmit={submit}>
            <button
              className={`drop-zone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
              type="button"
              onClick={() => inputRef.current?.click()}
              onDragOver={onDragOver}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <SelectedFileIcon file={file} />
              {file ? (
                <><strong>{file.name}</strong><span>{formatSize(file.size)} · 点击重新选择</span></>
              ) : (
                <><strong>拖入文件，或者点击选择</strong><span>支持 TXT、Markdown、图片和常见视频格式</span></>
              )}
            </button>
            <input
              ref={inputRef}
              className="visually-hidden"
              type="file"
              accept=".txt,.text,.md,.markdown,.png,.jpg,.jpeg,.gif,.bmp,.webp,.mp4,.mov,.mkv,.avi,.m4v,.mpeg,.mpg,.webm"
              onChange={(event) => chooseFile(event.target.files?.[0])}
            />

            <label className="instruction-field">
              <span>额外关注内容 <i>选填</i></span>
              <textarea
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="例如：重点识别用户依次打开的页面和完成的操作"
                rows={3}
              />
            </label>

            <button className="submit-upload" type="submit" disabled={!file || submitting}>
              {submitting ? <LoaderCircle className="spin" size={18} /> : <UploadCloud size={18} />}
              {submitting ? "正在解析并写入，请不要关闭页面" : "解析并写入记忆"}
            </button>

            {error && (
              <div className="upload-message error"><strong>导入失败</strong><span>{error}</span></div>
            )}
            {result && (
              <div className="upload-message success">
                <CheckCircle2 size={22} />
                <div>
                  <strong>{result.memories_created ? "已经写入 MemOS" : "文件已处理，但没有生成记忆"}</strong>
                  <span>{result.filename} · {formatSize(result.file_size || 0)} · {result.kind}</span>
                  <small>本次生成 {result.memories_created} 条记忆，Topic 处理 {result.topic.processed_memories} 条；当前有 {result.topic.active_topics} 个滚动席位。</small>
                  {result.topic.error && <small>记忆写入成功，但 Topic 更新失败：{result.topic.error}</small>}
                </div>
              </div>
            )}
          </form>

          <form className="remote-video-card panel" onSubmit={submitRemote}>
            <div><Link2 size={19} /><span><strong>导入远程视频</strong><small>直接粘贴 HTTP(S) 视频地址，本地视频仍使用上方文件上传。</small></span></div>
            <input value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} type="url" placeholder="https://example.com/video.mp4" />
            <button type="submit" disabled={!remoteUrl.trim() || remoteSubmitting}>{remoteSubmitting ? <LoaderCircle className="spin" size={16} /> : <Video size={16} />}{remoteSubmitting ? "正在解析" : "解析远程视频"}</button>
            {remoteResult && <p>已生成 {remoteResult.memories_created} 条记忆，Topic 处理 {remoteResult.topic.processed_memories} 条。</p>}
          </form>
          </div>

          <aside className="ingest-explainer">
            <p className="section-kicker">ONE ROUTE</p>
            <h2>前端不重新实现解析</h2>
            <p>上传页只负责发送文件。文件进入应用后端后，再由已有 Runtime 完成分类、解析和入库。</p>
            <ol>
              <li><b>01</b><span><strong>保存来源文件</strong>保留可追踪的本地来源。</span></li>
              <li><b>02</b><span><strong>Runtime 自动分类</strong>选择文字、图片、图文或视频路径。</span></li>
              <li><b>03</b><span><strong>MemOS 统一入库</strong>由应用后端沿用现有写入链路。</span></li>
              <li><b>04</b><span><strong>Topic 自动更新</strong>写入成功后，直接刷新外部滚动 Topic。</span></li>
            </ol>
            <div className="path-note">源文件保存位置和视频 OSS 上传都由应用后端配置，前端不接触服务器路径或密钥。</div>
          </aside>
        </div>
      </section>
    </main>
  );
}
