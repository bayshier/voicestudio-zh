import { useState } from 'react';
import {
  BookText,
  Code,
  Languages,
  Mic2,
  SlidersHorizontal,
  SpellCheck,
  Users,
} from 'lucide-react';

import VoiceSelector from '../VoiceSelector';
import SearchableSelect from '../SearchableSelect';
import AudiobookOverrides from './AudiobookOverrides';
import BookDetails from './BookDetails';
import CastPanel from './CastPanel';
import LexiconEditor from './LexiconEditor';
import ALL_LANGUAGES from '../../languages.json';
import { POPULAR_LANGS } from '../../utils/constants';

const FIELD_LABEL =
  'flex items-center gap-[5px] [font-family:var(--chrome-font-mono)] [font-size:var(--chrome-label-size)] font-semibold [letter-spacing:var(--chrome-label-track)] uppercase text-fg-muted';

const TOOL_BUTTON =
  'relative flex h-[46px] min-w-0 flex-col items-center justify-center gap-[3px] rounded-[8px] border border-transparent bg-transparent px-[6px] text-[0.62rem] text-fg-muted cursor-pointer transition-[background,color,box-shadow] duration-[120ms] hover:bg-[var(--chrome-hover-bg)] hover:text-fg focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:-2px] aria-pressed:bg-primary/[0.12] aria-pressed:text-primary aria-pressed:shadow-[inset_0_-2px_0_var(--color-brand)]';

function ToolButton({ active, badge = 0, icon, label, onClick }) {
  return (
    <button
      type="button"
      className={TOOL_BUTTON}
      aria-pressed={active}
      title={label}
      onClick={onClick}
    >
      {icon}
      <span className="w-full truncate text-center">{label}</span>
      {badge > 0 && (
        <span
          className="absolute right-[5px] top-[4px] min-w-[15px] rounded-full bg-[var(--chrome-hover-bg)] px-[4px] text-center text-[0.55rem] leading-[15px] text-fg [font-variant-numeric:tabular-nums]"
          aria-hidden="true"
        >
          {badge}
        </span>
      )}
    </button>
  );
}

/** Compact right-rail property inspector with one optional tool panel at a time. */
export default function AudiobookInspector({
  t,
  profiles,
  defaultVoice,
  setDefaultVoice,
  language,
  setLanguage,
  format,
  setFormat,
  loudness,
  setLoudness,
  castNames,
  voiceCast,
  setVoiceCast,
  overrides,
  setOverrides,
  emotionSupported,
  coverPreview,
  onCoverPick,
  clearCover,
  meta,
  setMetaField,
  lex,
  setLexRow,
  addLexRow,
  removeLexRow,
}) {
  // `undefined` means the user has not chosen a panel yet: Cast becomes the
  // useful default as soon as the script contains [voice:…] tags. Once the user
  // switches or closes a panel, their explicit choice wins.
  const [activePanel, setActivePanel] = useState(undefined);
  const panel =
    activePanel === undefined
      ? castNames.length > 0
        ? 'cast'
        : null
      : activePanel === 'cast' && castNames.length === 0
        ? null
        : activePanel;
  const togglePanel = (next) => setActivePanel(panel === next ? null : next);
  const detailCount =
    Object.values(meta).filter((value) => value?.trim()).length + (coverPreview ? 1 : 0);
  const lexiconCount = lex.filter((row) => row.word.trim() || row.say.trim()).length;
  const outputCount =
    (loudness !== 'off' ? 1 : 0) +
    (Object.values(overrides).some((value) => value !== null && value !== false && value !== '')
      ? 1
      : 0);

  return (
    <div className="flex flex-col gap-[9px] [container-type:inline-size] [container-name:audiobook-inspector]">
      <div className="grid grid-cols-1 gap-[8px] rounded-[11px] bg-[var(--chrome-bg)] p-[10px] @min-[360px]/audiobook-inspector:grid-cols-2 @min-[560px]/audiobook-inspector:[grid-template-columns:minmax(170px,1.35fr)_minmax(120px,0.9fr)_minmax(100px,0.7fr)]">
        <div className="flex min-w-0 flex-col gap-[4px] @min-[360px]/audiobook-inspector:col-span-2 @min-[560px]/audiobook-inspector:col-span-1">
          <label className={FIELD_LABEL}>
            <Mic2 size={11} aria-hidden="true" /> {t('audiobook.default_voice')}
          </label>
          <VoiceSelector
            value={defaultVoice}
            onChange={setDefaultVoice}
            profiles={profiles}
            defaultLabel={t('audiobook.engine_default')}
            ariaLabel={t('audiobook.default_voice')}
          />
        </div>

        <div className="flex min-w-0 flex-col gap-[4px]">
          <label className={FIELD_LABEL}>
            <Languages size={11} aria-hidden="true" /> {t('audiobook.language')}
          </label>
          <SearchableSelect
            value={language}
            options={ALL_LANGUAGES}
            popular={POPULAR_LANGS}
            recentsKey="omnivoice.recents.audiobookLang"
            onChange={setLanguage}
            ariaLabel={t('audiobook.language')}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-[4px]">
          <label className={FIELD_LABEL}>{t('audiobook.format')}</label>
          <select
            className="input-base"
            name="audiobook-format"
            value={format}
            onChange={(event) => setFormat(event.target.value)}
            aria-label={t('audiobook.format')}
          >
            <option value="m4b">{t('audiobook.format_m4b')}</option>
            <option value="mp3">{t('audiobook.format_mp3')}</option>
          </select>
        </div>
      </div>

      <div className="overflow-hidden rounded-[11px] bg-[var(--chrome-bg)] shadow-[inset_0_0_0_1px_var(--chrome-border)]">
        <div
          className="grid grid-cols-[repeat(auto-fit,minmax(68px,1fr))] gap-[2px] p-[4px]"
          role="toolbar"
          aria-label={t('audiobook.title')}
        >
          {castNames.length > 0 && (
            <ToolButton
              active={panel === 'cast'}
              badge={castNames.length}
              icon={<Users size={13} aria-hidden="true" />}
              label={t('audiobook.cast')}
              onClick={() => togglePanel('cast')}
            />
          )}
          <ToolButton
            active={panel === 'output'}
            badge={outputCount}
            icon={<SlidersHorizontal size={13} aria-hidden="true" />}
            label={t('audiobook.output')}
            onClick={() => togglePanel('output')}
          />
          <ToolButton
            active={panel === 'details'}
            badge={detailCount}
            icon={<BookText size={13} aria-hidden="true" />}
            label={t('audiobook.details')}
            onClick={() => togglePanel('details')}
          />
          <ToolButton
            active={panel === 'lexicon'}
            badge={lexiconCount}
            icon={<SpellCheck size={13} aria-hidden="true" />}
            label={t('audiobook.lexicon')}
            onClick={() => togglePanel('lexicon')}
          />
          <ToolButton
            active={panel === 'markup'}
            icon={<Code size={13} aria-hidden="true" />}
            label={t('audiobook.markup_help')}
            onClick={() => togglePanel('markup')}
          />
        </div>

        {panel && (
          <div className="border-t border-transparent p-[11px]">
            {panel === 'cast' && (
              <CastPanel
                t={t}
                castNames={castNames}
                voiceCast={voiceCast}
                setVoiceCast={setVoiceCast}
                profiles={profiles}
              />
            )}
            {panel === 'output' && (
              <div className="flex flex-col gap-[8px]">
                <div className="flex flex-col gap-[4px]">
                  <label className={FIELD_LABEL}>{t('audiobook.loudness')}</label>
                  <select
                    className="input-base"
                    name="audiobook-loudness"
                    value={loudness}
                    onChange={(event) => setLoudness(event.target.value)}
                    aria-label={t('audiobook.loudness')}
                  >
                    <option value="off">{t('audiobook.loudness_off')}</option>
                    <option value="acx">{t('audiobook.loudness_acx')}</option>
                    <option value="podcast">{t('audiobook.loudness_podcast')}</option>
                  </select>
                </div>
                <AudiobookOverrides
                  t={t}
                  overrides={overrides}
                  onChange={setOverrides}
                  emotionSupported={emotionSupported}
                />
              </div>
            )}
            {panel === 'details' && (
              <BookDetails
                t={t}
                coverPreview={coverPreview}
                onCoverPick={onCoverPick}
                clearCover={clearCover}
                meta={meta}
                setMetaField={setMetaField}
              />
            )}
            {panel === 'lexicon' && (
              <div className="flex flex-col gap-[6px]">
                <LexiconEditor
                  t={t}
                  lex={lex}
                  setLexRow={setLexRow}
                  addLexRow={addLexRow}
                  removeLexRow={removeLexRow}
                />
              </div>
            )}
            {panel === 'markup' && (
              <p className="m-0 text-[0.68rem] leading-[1.55] text-fg-muted">
                {t('audiobook.markup_hint')}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
