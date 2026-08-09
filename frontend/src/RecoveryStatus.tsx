import { useEffect, useState } from "react";

import {
  ApiError,
  type RecoveryReport,
  getRecoveryReport,
  runRecoveryCheck,
} from "./api";

interface RecoveryStatusProps {
  onError: (message: string) => void;
}

export function RecoveryStatus({ onError }: RecoveryStatusProps) {
  const [report, setReport] = useState<RecoveryReport | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getRecoveryReport(controller.signal)
      .then(setReport)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          onError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, [onError]);

  async function checkAgain() {
    setBusy(true);
    try {
      setReport(await runRecoveryCheck());
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const attention = report?.status === "needs_attention";
  return (
    <section className={`recovery-status ${attention ? "needs-attention" : ""}`}>
      <div>
        <p className="section-kicker">启动安全检查</p>
        <h2>{!report ? "正在检查本地工程" : attention ? "有项目需要人工检查" : "本地工程状态正常"}</h2>
        <p>{description(report)}</p>
      </div>
      <button type="button" className="quiet-button" disabled={busy} onClick={() => void checkAgain()}>
        {busy ? "检查中…" : "重新检查"}
      </button>
    </section>
  );
}

function description(report: RecoveryReport | null): string {
  if (!report) return "只核对本地数据库和文件，不会启动模型或付费请求。";
  if (report.status === "healthy") return "数据库、素材、导出和凭证边界均通过；没有启动外部请求。";
  const count =
    (report.queue_recovery?.needs_review ?? 0) +
    (report.integrity?.critical_findings ?? 0) +
    (report.integrity?.staging_items ?? 0);
  return `${count} 项需要处理；可恢复半成品已保留，付费任务没有自动重放。`;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "无法读取本地恢复检查结果。";
}
