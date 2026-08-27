import { LayoutDashboard, LogOut, MessageSquareText, Sparkles, Upload } from "lucide-react";
import { type ReactElement, useEffect, useState } from "react";

import { appApi } from "@/lib/api-client.ts";
import Link from "@/lib/link.tsx";

type AppRailProps = {
  active: "overview" | "runtime" | "topics" | "upload";
};

type ServiceState = "checking" | "online" | "degraded" | "offline";

const items = [
  { key: "overview", href: "/", label: "总览", icon: LayoutDashboard },
  { key: "runtime", href: "/runtime", label: "交互", icon: MessageSquareText },
  { key: "topics", href: "/topics", label: "Topic", icon: Sparkles },
  { key: "upload", href: "/upload", label: "上传", icon: Upload },
] as const;

function getServiceLabel(serviceState: ServiceState): string {
  switch (serviceState) {
    case "checking":
      return "正在检查";
    case "online":
      return "服务在线";
    case "degraded":
      return "依赖异常";
    case "offline":
      return "无法连接";
  }
}

export default function AppRail({ active }: AppRailProps): ReactElement {
  const [serviceState, setServiceState] = useState<ServiceState>("checking");

  useEffect(() => {
    let activeRequest = true;
    void appApi.health()
      .then((health) => {
        if (!activeRequest) return;
        setServiceState(health.status === "healthy" ? "online" : "degraded");
      })
      .catch(() => {
        if (activeRequest) setServiceState("offline");
      });
    return () => {
      activeRequest = false;
    };
  }, []);

  async function logout(): Promise<void> {
    try {
      await appApi.logout();
    } finally {
      window.location.replace("/login");
    }
  }

  return (
    <aside className="rail">
      <Link className="brand-mark" href="/" aria-label="MemOS 记忆运行台">M</Link>
      <nav aria-label="主导航">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = item.key === active;
          return (
            <Link
              className={`rail-item ${selected ? "active" : ""}`}
              href={item.href}
              aria-label={item.label}
              key={item.key}
            >
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
      <button className="rail-logout" type="button" onClick={() => void logout()} aria-label="退出登录">
        <LogOut aria-hidden="true" />
        <span>退出</span>
      </button>
      <div className="rail-health">
        <i className={serviceState === "online" ? "online" : ""} />
        <span>{getServiceLabel(serviceState)}</span>
      </div>
    </aside>
  );
}
