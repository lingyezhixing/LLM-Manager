import { Component, type ReactNode } from "react";

/** 渲染期错误兜底:捕获子树抛错 → 显示刷新页,防整棵 UI 白屏。
 * 置于 App 根(providers 之外),异常信息不落屏(仅复位)。 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-dvh flex-col items-center justify-center gap-4 bg-background px-4 text-center">
          <p className="text-base font-semibold text-foreground">界面出现异常</p>
          <button
            type="button"
            className="rounded-md border border-primary bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            onClick={() => window.location.reload()}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
