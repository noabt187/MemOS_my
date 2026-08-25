import Link from "@/lib/link";
import { Database, LayoutDashboard, LogOut, MessageSquareText, Sparkles, Upload } from "lucide-react";
import { appApi } from "@/lib/api-client";

type AppRailProps = {
  active: "overview" | "runtime" | "topics" | "upload";
  serviceHealthy?: boolean;
};

const items = [
  { key: "overview", href: "/", label: "总览", icon: LayoutDashboard },
  { key: "memories", href: "/#memories", label: "记忆", icon: Database },
  { key: "runtime", href: "/runtime", label: "交互", icon: MessageSquareText },
  { key: "topics", href: "/topics", label: "Topic", icon: Sparkles },
  { key: "upload", href: "/upload", label: "上传", icon: Upload },
] as const;

export default function AppRail({ active, serviceHealthy = true }: AppRailProps) {
  const logout = async () => {
    try {
      await appApi.logout();
    } finally {
      window.location.replace("/login");
    }
  };

  return (
    <aside className="rail">
      <Link className="brand-mark" href="/" aria-label="MemOS 记忆运行台">M</Link>
      <nav aria-label="主导航">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = item.key === active || (item.key === "memories" && active === "overview");
          return (
            <Link
              className={`rail-item ${selected && item.key !== "memories" ? "active" : ""}`}
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
        <i className={serviceHealthy ? "online" : ""} />
        <span>{serviceHealthy ? "服务在线" : "服务异常"}</span>
      </div>
    </aside>
  );
}
