/* ==========================================================================
   MemOS 检索异常诊断 · 页面交互
   数据为 2026-08-17 本地实测快照；实时探测受浏览器 CORS 限制时优雅降级。
   ========================================================================== */
(() => {
  "use strict";

  /* ---------------- 数据（实测快照） ---------------- */

  const SNAPSHOT = "数据快照 2026-08-17 · 本地实测";

  const BAD_NODES = [
    {
      id: "688b4c53-3fd4-42ad-a963-9bfb6da46210",
      type: "LongTermMemory",
      session: "terminal-e60ff729",
      created: "2026-08-13 11:33:33 (北京时间)",
      memory: "user: [2026-08-13 03:33:33]: 分析一下我2026年7月11日的睡眠状况",
      bad: '{"ingest_batch_id": "cc1fb154-5c68-4048-af54-c9b6b2cd5a6d"}',
      good: '{ "ingest_batch_id": "cc1fb154-5c68-4048-af54-c9b6b2cd5a6d" }',
    },
    {
      id: "8a433b4f-9c44-45a7-9de2-cc6346131016",
      type: "LongTermMemory",
      session: "terminal-e60ff729",
      created: "2026-08-13 11:33:33 (北京时间)",
      memory: "整体评价 您当天的综合睡眠评分为 **74分**，评级为\u201c睡眠一般\u201d…",
      bad: '{"ingest_batch_id": "cc1fb154-5c68-4048-af54-c9b6b2cd5a6d"}',
      good: '{ "ingest_batch_id": "cc1fb154-5c68-4048-af54-c9b6b2cd5a6d" }',
    },
  ];

  const TIMELINE = [
    {
      time: "08-13 11:29–11:31",
      title: "导入睡眠截图，生成记忆",
      desc: "“这张图片是手机健康应用睡眠监测页面的截图，展示 2026-07-11 睡眠记录”写入 Neo4j + Qdrant（normal，带向量）。",
      cls: "tl-item--good",
    },
    {
      time: "08-13 11:33:0x",
      title: "当场检索成功",
      desc: "你问“分析一下 7 月 11 日的睡眠状况”，助手检索到上面的截图记忆并答出“睡眠评分 74 分”。此时两个坏节点尚未写入。",
      cls: "tl-item--good",
    },
    {
      time: "08-13 11:33:33",
      title: "坏节点诞生：对话被回写为记忆",
      desc: "该对话的用户问题与助手回答被回写为 688b4c53 / 8a433b4f，internal_info 被写库端 json.dumps 成字符串。从此每次检索的候选窗口都可能命中它们。",
      cls: "tl-item--bad",
    },
    {
      time: "08-17（重启后再次使用）",
      title: "每条检索都为空",
      desc: "默认 dedup=mmr 放大 top_k → 必然命中坏节点 → from_dict 抛 ValidationError → 被 _search_text 静默吞掉 → HTTP 200 + text_mem: []。",
      cls: "tl-item--bad",
    },
    {
      time: "修复后",
      title: "数据修复 → 检索立即恢复",
      desc: "清掉/还原坏节点的 internal_info 字段（或代码读端还原），dedup=no 与默认参数均可正常返回 0.7+ 相关度结果。",
      cls: "tl-item--warn",
    },
  ];

  const REPRO = {
    default: {
      verdict: { text: "默认参数（dedup=mmr, rerank=true, relativity=0.45）→ text_mem: []（空）", cls: "bad" },
      note: "top_k 被放大到 15，候选窗口覆盖两个坏节点 → 反序列化抛 ValidationError → 异常被吞 → 空结果。",
      items: [],
    },
    nodup: {
      verdict: { text: "dedup=no（top_k=5, rerank=true, relativity=0.45）→ 3 条命中", cls: "ok" },
      note: "top_k 保持 5，候选窗口避开坏节点 → 正常返回，相关度 0.70+。",
      items: [
        { score: 0.7555, text: "我周五去杭州" },
        { score: 0.7084, text: "用户计划在2026年8月14日（周五）前往杭州" },
        { score: 0.7082, text: "用户计划在2026年8月14日（周五）前往杭州" },
      ],
    },
  };

  /* ---------------- 工具函数 ---------------- */

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  /* ---------------- 顶部：快照日期 ---------------- */
  const dateEl = $("#snapshotDate");
  if (dateEl) dateEl.textContent = SNAPSHOT;

  /* ---------------- 坏节点卡片 ---------------- */
  const nodesWrap = $("#badNodes");
  if (nodesWrap) {
    nodesWrap.innerHTML = BAD_NODES.map(
      (n) => `
      <article class="node-card">
        <span class="node-id">${escapeHtml(n.id)}</span>
        <div class="node-meta">
          <span class="tag">${escapeHtml(n.type)}</span>
          <span class="tag">session: ${escapeHtml(n.session)}</span>
          <span class="tag">created: ${escapeHtml(n.created)}</span>
        </div>
        <p class="node-memory">${escapeHtml(n.memory)}</p>
        <span class="field-label">internal_info（实际入库值 · 字符串 ✗）</span>
        <pre class="field-bad">${escapeHtml(n.bad)}</pre>
        <div class="arrow-down" aria-hidden="true">▼</div>
        <span class="field-label">internal_info（模型期望值 · 字典 ✓）</span>
        <pre class="field-good">${escapeHtml(n.good)}</pre>
      </article>`
    ).join("");
  }

  /* ---------------- 时间线 ---------------- */
  const tlWrap = $("#timeline");
  if (tlWrap) {
    tlWrap.innerHTML = TIMELINE.map(
      (t) => `
      <li class="tl-item ${t.cls}">
        <span class="tl-time">${escapeHtml(t.time)}</span>
        <p class="tl-title">${escapeHtml(t.title)}</p>
        <p class="tl-desc">${escapeHtml(t.desc)}</p>
      </li>`
    ).join("");
  }

  /* ---------------- 链路节点：点击显示说明 + 故障报错 ---------------- */
  const steps = $$(".step");
  const errorBox = $("#chainError");
  steps.forEach((step) => {
    step.setAttribute("tabindex", "0");
    step.addEventListener("click", () => {
      const wasActive = step.classList.contains("is-active");
      steps.forEach((s) => s.classList.remove("is-active"));
      if (!wasActive) {
        step.classList.add("is-active");
        const desc = step.getAttribute("data-desc");
        if (desc && errorBox) {
          errorBox.querySelector(".callout strong").textContent = "该环节说明：";
          errorBox.querySelector("pre").textContent = desc;
          errorBox.hidden = false;
        }
      } else if (errorBox) {
        errorBox.hidden = true;
      }
    });
    step.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        step.click();
      }
    });
  });
  // 故障节点默认展开
  const fault = $(".step--fault");
  if (fault && errorBox) {
    fault.click();
    const desc = fault.getAttribute("data-desc");
    errorBox.querySelector(".callout strong").textContent = "该环节说明：";
    errorBox.querySelector("pre").textContent = desc;
  }

  /* ---------------- 复现实验 ---------------- */
  const reproBtns = $$(".repro-btn");
  const reproResult = $("#reproResult");
  reproBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      reproBtns.forEach((b) => b.setAttribute("aria-pressed", "false"));
      btn.setAttribute("aria-pressed", "true");
      const data = REPRO[btn.dataset.case];
      if (!data || !reproResult) return;
      const items = data.items.length
        ? `<ul class="repro-list">${data.items
            .map(
              (it) => `<li><span class="repro-score">${it.score.toFixed(4)}</span><span>${escapeHtml(it.text)}</span></li>`
            )
            .join("")}</ul>`
        : "";
      reproResult.innerHTML = `
        <p class="repro-verdict ${data.verdict.cls}">${data.verdict.text}</p>
        <p class="repro-hint">${escapeHtml(data.note)}</p>
        ${items}`;
    });
  });

  /* ---------------- 修复方案 tabs ---------------- */
  const tabs = $$(".tab");
  const panels = $$(".tab-panel");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.setAttribute("aria-selected", "false"));
      panels.forEach((p) => (p.hidden = true));
      tab.setAttribute("aria-selected", "true");
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      if (panel) panel.hidden = false;
    });
    tab.addEventListener("keydown", (e) => {
      const idx = tabs.indexOf(tab);
      let next = null;
      if (e.key === "ArrowRight") next = tabs[(idx + 1) % tabs.length];
      if (e.key === "ArrowLeft") next = tabs[(idx - 1 + tabs.length) % tabs.length];
      if (next) {
        e.preventDefault();
        next.focus();
        next.click();
      }
    });
  });

  /* ---------------- 复制按钮 ---------------- */
  $$(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const targetId = btn.dataset.copyTarget;
      const pre = document.getElementById(targetId);
      if (!pre) return;
      const text = pre.textContent.trim();
      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch (_e) {
        // fallback for non-secure contexts
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          ok = true;
        } catch (_e2) {
          ok = false;
        }
        document.body.removeChild(ta);
      }
      if (ok) {
        const original = btn.textContent;
        btn.textContent = "已复制 ✓";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = original;
          btn.classList.remove("copied");
        }, 1600);
      }
    });
  });

  /* ---------------- 主题切换（浅色/白底 <-> 深色） ---------------- */
  const themeToggle = $("#themeToggle");
  const updateThemeUI = () => {
    if (!themeToggle) return;
    const isLight = document.body.dataset.theme === "light";
    themeToggle.setAttribute("aria-pressed", String(isLight));
    const icon = themeToggle.querySelector(".theme-toggle-icon");
    const label = themeToggle.querySelector(".theme-toggle-text");
    if (icon) icon.textContent = isLight ? "🌙" : "☀️";
    if (label) label.textContent = isLight ? "深色" : "浅色";
  };
  if (themeToggle) {
    updateThemeUI();
    themeToggle.addEventListener("click", () => {
      const next = document.body.dataset.theme === "light" ? "dark" : "light";
      document.body.setAttribute("data-theme", next);
      try {
        localStorage.setItem("memory-diag-theme", next);
      } catch (e) { /* 存储不可用时仅本次生效 */ }
      updateThemeUI();
    });
  }

  /* ---------------- 目录：点击跳转 + IntersectionObserver 高亮 ---------------- */
  const tocLinks = $$(".toc-list a");
  if (tocLinks.length) {
    const tocSections = tocLinks
      .map((a) => document.getElementById(a.getAttribute("href").slice(1)))
      .filter(Boolean);

    const setCurrent = (id) => {
      tocLinks.forEach((a) => {
        const isCurrent = a.getAttribute("href") === "#" + id;
        a.classList.toggle("is-current", isCurrent);
        if (isCurrent) a.setAttribute("aria-current", "true");
        else a.removeAttribute("aria-current");
      });
    };

    // 点击目录链接：阻止默认锚点导航，用 JS 平滑滚动
    tocLinks.forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const id = a.getAttribute("href").slice(1);
        const target = document.getElementById(id);
        if (!target) return;
        const headerH = document.querySelector(".topbar")?.offsetHeight || 0;
        const y = target.getBoundingClientRect().top + window.scrollY - headerH - 20;
        window.scrollTo({ top: y, behavior: "smooth" });
        setCurrent(id);
      });
    });

    // IntersectionObserver 高亮当前章节
    const headerH = document.querySelector(".topbar")?.offsetHeight || 0;
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length) setCurrent(visible[0].target.id);
      },
      { rootMargin: `-${headerH + 20}px 0px -55% 0px`, threshold: 0 }
    );
    tocSections.forEach((s) => obs.observe(s));
    if (tocSections.length) setCurrent(tocSections[0].id);
  }

  /* ---------------- API 实时探测（CORS 受限时优雅降级） ---------------- */
  const probePill = document.createElement("span");
  probePill.className = "badge badge--info";
  probePill.textContent = "API 探测中…";
  const meta = $(".topbar-meta");
  if (meta) meta.appendChild(probePill);

  fetch("http://127.0.0.1:8000/health", { method: "GET", mode: "cors" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
    .then((j) => {
      probePill.textContent = `API 在线 · ${j.service || "memos"} ${j.version || ""}`;
      probePill.classList.remove("badge--info");
      probePill.classList.add("badge--ok");
    })
    .catch(() => {
      probePill.textContent = "API 探测受限（浏览器 CORS）";
      probePill.title =
        "如需实时模式，请在 MemOS .env 配置 CORS_ORIGINS=http://127.0.0.1:8090 并重启 API";
    });
})();
