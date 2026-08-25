import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import Home from "@/app/page";
import LoginPage from "@/app/login/page";
import RuntimePage from "@/app/runtime/page";
import TopicsPage from "@/app/topics/page";
import UploadPage from "@/app/upload/page";
import { appApi } from "@/lib/api-client";
import "@/app/globals.css";


const ROUTES = {
  "/": { title: "记忆运行台", page: Home },
  "/runtime": { title: "记忆交互", page: RuntimePage },
  "/topics": { title: "Topic", page: TopicsPage },
  "/upload": { title: "上传到记忆", page: UploadPage },
} as const;


function loginUrl() {
  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  return `/login?return_to=${encodeURIComponent(returnTo)}`;
}


function Application() {
  const pathname = window.location.pathname.replace(/\/$/, "") || "/";
  const loginPage = pathname === "/login";
  const [checking, setChecking] = useState(!loginPage);

  useEffect(() => {
    if (loginPage) {
      document.title = "登录 · MemOS 记忆运行台";
      return;
    }
    const route = ROUTES[pathname as keyof typeof ROUTES] || ROUTES["/"];
    document.title = `${route.title} · MemOS`;
    void appApi.session()
      .then(({ authenticated }) => {
        if (!authenticated) window.location.replace(loginUrl());
        else setChecking(false);
      })
      .catch(() => window.location.replace(loginUrl()));
  }, [loginPage, pathname]);

  if (loginPage) return <LoginPage />;
  if (checking) {
    return (
      <main className="login-shell">
        <section className="login-card"><p className="eyebrow">MEMOS</p><h1>正在验证登录状态</h1></section>
      </main>
    );
  }
  const route = ROUTES[pathname as keyof typeof ROUTES];
  if (!route) {
    window.history.replaceState(null, "", "/");
    return <Home />;
  }
  const Page = route.page;
  return <Page />;
}


const root = document.getElementById("root");
if (!root) throw new Error("页面缺少 root 容器。")
createRoot(root).render(<StrictMode><Application /></StrictMode>);
