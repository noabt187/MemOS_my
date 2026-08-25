"use client";

import { FormEvent, useState } from "react";
import { KeyRound, LoaderCircle, LockKeyhole, ShieldCheck } from "lucide-react";
import { appApi } from "@/lib/api-client";

function safeReturnPath() {
  const value = new URLSearchParams(window.location.search).get("return_to") || "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || loading) return;
    setLoading(true);
    setError("");
    try {
      await appApi.login(password);
      window.location.replace(safeReturnPath());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "认证失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="login-mark"><LockKeyhole size={27} /></div>
        <p className="eyebrow">PRIVATE MEMORY ACCESS</p>
        <h1>进入记忆运行台</h1>
        <p>这是一个私人 MemOS 服务。输入共享访问密码后，才能查看和操作其中的数据。</p>
        <form onSubmit={submit}>
          <label>
            <span>访问密码</span>
            <div><KeyRound size={16} /><input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="请输入访问密码" /></div>
          </label>
          {error && <div className="login-error">{error}</div>}
          <button disabled={!password || loading}>
            {loading ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={17} />}
            {loading ? "正在验证" : "验证并进入"}
          </button>
        </form>
        <small>登录状态在当前浏览器中保留 7 天。不要在公共设备上保存密码。</small>
      </section>
      <aside className="login-context">
        <span>MEMOS / PRIVATE NODE</span>
        <strong>你的记忆，不应该成为公开接口。</strong>
        <p>前端页面、写入、搜索、对话、上传与 Topic 操作使用同一层认证保护。</p>
      </aside>
    </main>
  );
}
