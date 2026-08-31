import React from 'react';
import { RefreshCw, CheckCircle, Download } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Button } from '../../../ui';

/**
 * "For your system" banner — the device's curated model preset (GET
 * /setup/recommendations): device label as title, the preset rationale as
 * caption, one-click install per row, plus required-only and
 * download-all-remaining bulk actions. Purely presentational; all state and
 * mutations are supplied by the host ModelStoreTab.
 */
export default function RecoBanner({
  reco,
  t,
  installMutation,
  installingReco,
  setInstallingReco,
  onInstallRecommended,
  // One-click single-model install (host's withBusy flow). Optional — legacy
  // callers without it render the rows without the per-row action.
  onInstall,
  // Live per-repo runtime (SSE download state) so a row's install button flips
  // to a busy state while its download runs. Optional.
  getRowRuntime,
  // Free space (GB) on the model-cache volume, from GET /models — gives the
  // download buttons context and warns BEFORE a doomed multi-GB download.
  diskFreeGb = null,
}) {
  if (!reco) return null;
  if (reco.all_installed) {
    return (
      <div className="mb-[var(--space-2)] flex items-center gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] [border:1px_solid] [border-left-width:2px] [border-color:color-mix(in_srgb,#8ec07c_30%,transparent)] [border-left-color:#8ec07c] bg-[color-mix(in_srgb,#8ec07c_4%,transparent)] px-[var(--space-4)] py-[var(--space-2)] text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)]">
        <CheckCircle size={12} color="#8ec07c" />
        <span className="flex-1">
          {t('models.reco_installed_for', { device: reco.device.label })}
        </span>
        <span className="text-[length:var(--text-2xs)] text-[var(--chrome-fg-dim)]">
          {reco.total_gb} GB
        </span>
      </div>
    );
  }
  // Any per-row install in flight? Bulk actions are disabled while one runs
  // (and rows are disabled while a bulk runs) so the same repo can't be
  // double-installed from both paths.
  const anyRowActive =
    !!getRowRuntime &&
    reco.models.some((m) => {
      const rt = getRowRuntime(m);
      return !!(rt?.showBar || rt?.isInstalling || rt?.rowBusy);
    });
  return (
    <div className="mb-[var(--space-2)] flex flex-col items-stretch gap-[var(--space-2)] rounded-[var(--chrome-radius-pill)] [border:1px_solid] [border-left-width:2px] [border-color:color-mix(in_srgb,#f3a5b6_25%,transparent)] [border-left-color:#f3a5b6] bg-[linear-gradient(135deg,color-mix(in_srgb,#f3a5b6_4%,transparent),color-mix(in_srgb,#d3869b_2%,transparent))] px-[var(--space-4)] pb-[var(--space-4)] pt-[var(--space-3)] text-[length:var(--text-xs)] text-[var(--chrome-fg-muted)] shadow-[0_0_12px_color-mix(in_srgb,#f3a5b6_6%,transparent)]">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[length:var(--text-md)] font-semibold text-[var(--chrome-fg)]">
          {t('models.reco_for', { device: reco.device.label })}
        </span>
        <div className="flex flex-shrink-0 gap-1">
          {(() => {
            const requiredMissing = reco.models.filter((m) => m.required && !m.installed);
            const requiredGb = requiredMissing.reduce((s, m) => s + m.size_gb, 0);
            if (requiredMissing.length === 0) return null;
            return (
              <Button
                variant="primary"
                size="sm"
                onClick={async () => {
                  setInstallingReco(true);
                  try {
                    await Promise.all(
                      requiredMissing.map((m) => installMutation.mutateAsync(m.repo_id)),
                    );
                    toast.success(
                      t('models.started_downloading_required', { count: requiredMissing.length }),
                    );
                  } catch (e) {
                    toast.error(t('models.install_failed', { message: e.message || e }));
                  } finally {
                    setInstallingReco(false);
                  }
                }}
                disabled={installingReco || anyRowActive}
                leading={installingReco ? <RefreshCw size={12} className="spinner" /> : null}
              >
                {installingReco
                  ? t('models.starting')
                  : t('models.required_size', { size: requiredGb.toFixed(1) })}
              </Button>
            );
          })()}
          <Button
            variant="subtle"
            size="sm"
            onClick={onInstallRecommended}
            disabled={installingReco || anyRowActive}
            title={t('models.download_all_remaining_title')}
          >
            {t('models.all_size', { size: reco.download_gb_remaining })}
          </Button>
        </div>
      </div>
      {/* Why these picks — the backend's per-platform rationale. */}
      {reco.rationale && (
        <p
          className="m-0 -mt-[2px] text-[length:var(--text-xs)] leading-[1.5] text-[var(--chrome-fg-muted)]"
          data-testid="reco-rationale"
        >
          {reco.rationale}
        </p>
      )}
      {/* Disk context next to the download actions: how much room the
          download has, and a plain warning when it won't fit. */}
      {diskFreeGb != null && (
        <div
          className="-mt-[2px] text-[length:var(--text-2xs)] text-[var(--chrome-fg-dim)]"
          data-testid="reco-disk-context"
        >
          {t('models.reco_disk_free', { free: diskFreeGb })}
        </div>
      )}
      {diskFreeGb != null && Number(reco.download_gb_remaining) > Number(diskFreeGb) && (
        <div
          role="alert"
          className="text-[length:var(--text-xs)] text-[var(--chrome-severity-warn)]"
          data-testid="reco-low-disk"
        >
          {t('models.reco_low_disk', {
            need: reco.download_gb_remaining,
            free: diskFreeGb,
          })}
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-[var(--space-5)] gap-y-0 text-[length:var(--text-sm)] leading-[1.6]">
        {reco.models.map((m) => {
          const rt = getRowRuntime?.(m);
          const rowActive = !!(rt?.showBar || rt?.isInstalling || rt?.rowBusy);
          return (
            <span
              key={m.repo_id}
              className={`inline-flex items-center gap-1 overflow-hidden text-ellipsis whitespace-nowrap ${
                m.installed ? 'text-[var(--chrome-fg)]' : 'text-[var(--chrome-fg-muted)]'
              }`}
            >
              {m.installed ? '✓' : '○'} {m.label}
              <span className="font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-2xs)] text-[var(--chrome-fg-dim)]">
                {m.size_gb}
              </span>
              {m.required && (
                <span className="rounded-[999px] [border:1px_solid_color-mix(in_srgb,#d3869b_30%,transparent)] px-[3px] py-0 text-[length:var(--text-2xs)] uppercase leading-[1.5] tracking-[0.04em] text-[#d3869b]">
                  {t('models.req_tag')}
                </span>
              )}
              {/* One-click install for a single missing pick (reuses the host's
                  install flow — progress shows on the catalog row below too). */}
              {!m.installed &&
                onInstall &&
                (rowActive ? (
                  <RefreshCw
                    size={10}
                    className="spinner shrink-0 text-[var(--chrome-accent)]"
                    aria-label={t('models.downloading')}
                  />
                ) : (
                  <Button
                    variant="icon"
                    iconSize="sm"
                    onClick={() => onInstall(m.repo_id)}
                    disabled={installingReco}
                    title={t('models.install_btn')}
                    aria-label={t('models.reco_install_one', { label: m.label })}
                    data-testid={`reco-install-${m.repo_id}`}
                  >
                    <Download size={11} />
                  </Button>
                ))}
            </span>
          );
        })}
      </div>
    </div>
  );
}
